#!/usr/bin/env python3
"""Guard the M4-2 authorization and lease planning freeze."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-2"
EXPECTED_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
BRANCH = "aota/m4/m4-2-authorization-planning"
REQUIRED = {
    "m4-2-current-authorization-capability-audit.json",
    "m4-2-trusted-mutation-authorization-contract.json",
    "m4-2-capability-lease-issuance-contract.json",
    "m4-2-authorization-error-contract.json",
    "m4-2-m4-3-handoff.json",
    "m4-2-source-ownership-forecast.json",
    "m4-2-regression-handoff-map.json",
}
ALLOWED_PREFIXES = ("deploy/evidence/issues/9/m4-2/",)
ALLOWED_FILES = {"scripts/m4_2_authorization_plan_guard.py"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_required() -> dict[str, dict]:
    require(EVIDENCE.is_dir(), "M4-2 evidence directory is missing")
    actual = {path.name for path in EVIDENCE.glob("*.json")}
    require(actual == REQUIRED, f"required artifact set mismatch: {sorted(actual)}")
    result = {}
    for name in sorted(REQUIRED):
        path = EVIDENCE / name
        with path.open(encoding="utf-8") as stream:
            result[name] = json.load(stream)
    return result


def assert_metadata(document: dict, name: str) -> None:
    require(document.get("project_id") == "aota_forge", f"{name}: project")
    require(document.get("governing_plan_issue") == "wzjcccc-dotcom/aota-hermes-tools#9", f"{name}: issue")
    require(document.get("milestone") == "M4", f"{name}: milestone")
    require(document.get("work_item") == "M4-2-P0", f"{name}: work item")
    require(document.get("expected_base_sha") == EXPECTED_BASE, f"{name}: exact expected base")
    require(document.get("planning_only") is True, f"{name}: planning only")
    require(document.get("production_behavior_claimed") is False, f"{name}: production claim")


def assert_protocol(docs: dict[str, dict]) -> None:
    audit = docs["m4-2-current-authorization-capability-audit.json"]
    authorization = docs["m4-2-trusted-mutation-authorization-contract.json"]
    lease = docs["m4-2-capability-lease-issuance-contract.json"]
    errors = docs["m4-2-authorization-error-contract.json"]
    handoff = docs["m4-2-m4-3-handoff.json"]
    forecast = docs["m4-2-source-ownership-forecast.json"]
    regression = docs["m4-2-regression-handoff-map.json"]

    for name, document in docs.items():
        assert_metadata(document, name)

    entries = audit.get("entries", [])
    required_components = {
        "AuthorityEngine", "AuthorityRequest", "CapabilityLease", "lease validation",
        "lease issuance", "lease expiry", "lease consumption", "lease revocation",
        "authorization evidence", "approval evidence", "MaterializedDecisionEvidence",
        "MutationIntent", "intent fingerprint", "Subject revision binding",
        "external authority precondition binding", "contract hash binding", "trusted principal",
        "context resolver", "idempotency", "transaction-local lease consumption",
        "unknown-outcome recovery",
    }
    require(len(entries) >= len(required_components), "audit entry count")
    require(required_components <= {entry.get("component") for entry in entries}, "audit coverage")
    for entry in entries:
        require({"component", "source_evidence", "current_capability", "current_limit", "classification", "production_behavior_claimed"} <= entry.keys(), "audit entry shape")
        require(entry["classification"] in {"REUSE_AS_IS", "EXTEND_IN_M4_2", "CONSUME_LATER", "OUT_OF_SCOPE_M4_2"}, "audit classification")
        require(entry["production_behavior_claimed"] is False, "audit production claim")

    freeze = authorization["freeze"]
    require(freeze["TRUSTED_MUTATION_AUTHORIZATION_IS_SEMANTIC_DECISION"] == "no", "authorization is semantic decision")
    require(freeze["DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED"] == "yes", "durable evidence")
    require(freeze["CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY"] == "no", "durable lease")
    require(freeze["MODEL_MAY_SELF_ASSERT_TRUSTED_PRINCIPAL"] == "no", "model principal assertion")
    require(freeze["MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE"] == "no", "model lease assertion")
    required_binding = set(freeze["required_binding_fields"])
    require({"principal", "operation", "typed_target", "mutation_scope", "contract_hash", "intent_fingerprint", "subject_expected_revision", "external_authority_precondition_digest_or_reference", "authorization_basis", "issuance_correlation_or_reservation_reference", "issued_at", "expires_at"} <= required_binding, "authorization binding")
    taxonomy = {item["basis"] for item in freeze["authorization_basis_taxonomy"]}
    require(taxonomy == {"trusted_scope_no_extra_approval", "approval_evidence", "materialized_decision_evidence", "project_milestone_semantic_decision"}, "authorization taxonomy")
    distinctions = freeze["evidence_distinctions"]
    require(distinctions["APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE"] == "no", "approval decision collapse")
    require(distinctions["approval_evidence_may_substitute_for_materialized_decision"] == "no", "approval substitution")
    domains = freeze["revision_domains"]
    require(domains["SUBJECT_REVISION"] and domains["AUTHORITY_SOURCE_REVISION"] and domains["NORMALIZED_PLAN_DIGEST"], "revision domains")
    require(domains["RAW_SOURCE_DIGEST_DIFFERS_FROM_NORMALIZED_PLAN_DIGEST"] == "yes", "raw normalized distinction")
    require(domains["normalized_plan_digest_may_substitute_for_external_cas"] == "no", "normalized external CAS")

    issuer = lease["issuer_boundary"]
    require(issuer["LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER"] == "no", "issuer semantic decision")
    for forbidden in ("choose project", "choose a Subject under ambiguity", "invent mutation scope", "widen authority", "approve a Milestone", "create a semantic Decision", "select rollback", "rebase intent", "change candidate semantics"):
        require(forbidden in issuer["issuer_may_not"], f"issuer prohibition: {forbidden}")
        require(forbidden not in issuer["issuer_may"], f"issuer widened capability: {forbidden}")
    for allowed in ("validate an already-authorized exact mutation", "validate descriptor requirements", "validate trusted principal and exact typed target", "bind exact operation and mutation scope", "bind exact intent_fingerprint", "bind Subject and external authority preconditions", "issue a bounded short-lived lease"):
        require(allowed in issuer["issuer_may"], f"issuer capability: {allowed}")
    exact = lease["exact_binding"]
    require(set(["principal", "operation", "typed_target", "mutation_scope", "contract_hash", "intent_fingerprint", "subject_expected_revision", "external_authority_precondition_digest_or_reference", "authorization_basis", "reservation_or_candidate_identity", "issued_at", "expires_at"]) <= set(exact["required_fields"]), "lease exact binding")
    require(exact["scope_expansion_allowed"] == "no" and exact["intent_rebase_allowed"] == "no" and exact["contract_drift_allowed"] == "no", "lease widening")
    lifecycle = lease["lifecycle"]
    require(set(["issued", "valid", "consumed", "expired", "revoked"]) <= set(lifecycle["states"]), "lease states")
    for key in ("CONSUMED_LEASE_REUSE_ALLOWED", "EXPIRED_LEASE_REUSE_ALLOWED", "REVOKED_LEASE_REUSE_ALLOWED", "UNKNOWN_OUTCOME_BLIND_LEASE_REUSE", "LEASE_MAY_CROSS_UNKNOWN_OUTCOME_RETRY"):
        require(lifecycle[key] == "no", f"lease reuse: {key}")
    unknown = lease["unknown_outcome_protocol"]
    require(unknown["OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION"] == "yes", "unknown reconciliation")
    require(unknown["prior_attempt_reuse"] == "no blind reuse of the original lease", "unknown lease reuse")
    require(unknown["third_external_state"] == "CONFLICT; no automatic overwrite or reissue", "unknown third state")
    require(lease["scope_boundary"]["external_authority_write_implemented_in_m4_2"] == "no", "external write")
    require(lease["scope_boundary"]["write_ingress_implemented_in_m4_2"] == "no", "write ingress")

    codes = {item["code"] for item in errors["errors"]}
    required_codes = {"AUTHORIZATION_MISSING", "AUTHORIZATION_SCOPE_MISMATCH", "AUTHORIZATION_TARGET_MISMATCH", "AUTHORIZATION_OPERATION_MISMATCH", "AUTHORIZATION_CONTRACT_DRIFT", "APPROVAL_REQUIRED", "MATERIALIZED_DECISION_REQUIRED", "SUBJECT_REVISION_STALE", "AUTHORITY_PRECONDITION_STALE", "LEASE_EXPIRED", "LEASE_REVOKED", "LEASE_CONSUMED", "LEASE_INTENT_MISMATCH", "LEASE_TARGET_MISMATCH", "LEASE_SCOPE_MISMATCH", "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION", "NEEDS_SEMANTIC_CHOICE"}
    require(required_codes <= codes, "canonical error coverage")
    require(errors["distinctions"]["NEEDS_SEMANTIC_CHOICE_is_authority_failure"] == "no", "semantic choice collapse")
    require(errors["distinctions"]["APPROVAL_REQUIRED_equals_MATERIALIZED_DECISION_REQUIRED"] == "no", "error basis collapse")
    require(errors["distinctions"]["SUBJECT_REVISION_STALE_equals_AUTHORITY_PRECONDITION_STALE"] == "no", "error domain collapse")

    require(handoff["acceptance_gate"]["M4_3_EXECUTION_AUTHORIZED"] == "no", "M4-3 authorization")
    require(handoff["acceptance_gate"]["M4_2_EXECUTION_AUTHORIZED"] == "no", "M4-2 authorization")
    require(len(handoff["M4_3_receives"]) >= 5 and len(handoff["M4_3_must_not"]) >= 7, "M4-3 handoff coverage")
    require("mint semantic authority" in handoff["M4_3_must_not"], "M4-3 mint authority")
    require("turn descriptor requirements into permission" in handoff["M4_3_must_not"], "M4-3 descriptor permission")

    require(forecast["M4_2_M4_4_SOURCE_PARALLEL_SAFETY"] == "pending", "source safety state")
    require(forecast["source_parallelism_authorized"] == "no", "source parallel authorization")
    forecast_paths = {entry["path"] for entry in forecast["entries"]}
    require("aota_forge/core/authority.py" in forecast_paths and "aota_forge/core/context.py" in forecast_paths and "aota_forge/core/contracts/__init__.py" in forecast_paths, "hot file forecast")
    require("yes" in {entry["possible_m4_4_overlap"][:3] for entry in forecast["entries"]}, "M4-4 overlap forecast")

    regression_classes = {entry["failure_class"] for entry in regression["entries"]}
    require({"WCTX-1", "BIND-1", "DRIFT-1", "RECOVERY-1", "RC2-1", "B014-F", "B011", "B013", "B014"} <= regression_classes, "regression handoff coverage")
    require(regression["M4_2_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT"] == 0, "behavioral closure false claim count")
    require(regression["behavioral_closure_claimed"] == "no", "behavioral closure claim")


def negative_cases(docs: dict[str, dict]) -> int:
    cases: list[tuple[str, callable]] = []

    def altered(mutator) -> dict[str, dict]:
        candidate = deepcopy(docs)
        mutator(candidate)
        return candidate

    cases.append(("NEG-M42-01", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["issuer_boundary"].update(LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER="yes")))
    cases.append(("NEG-M42-02", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["issuer_boundary"]["issuer_may"].append("widen authority")))
    cases.append(("NEG-M42-03", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["exact_binding"]["required_fields"].remove("intent_fingerprint")))
    cases.append(("NEG-M42-04", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["exact_binding"]["required_fields"].remove("typed_target")))
    cases.append(("NEG-M42-05", lambda d: d["m4-2-trusted-mutation-authorization-contract.json"]["freeze"]["evidence_distinctions"].update(APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE="yes")))
    cases.append(("NEG-M42-06", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["lifecycle"].update(EXPIRED_LEASE_REUSE_ALLOWED="yes")))
    cases.append(("NEG-M42-07", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["lifecycle"].update(CONSUMED_LEASE_REUSE_ALLOWED="yes")))
    cases.append(("NEG-M42-08", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["lifecycle"].update(UNKNOWN_OUTCOME_BLIND_LEASE_REUSE="yes")))
    cases.append(("NEG-M42-09", lambda d: d["m4-2-trusted-mutation-authorization-contract.json"]["freeze"]["revision_domains"].update(normalized_plan_digest_may_substitute_for_external_cas="yes")))
    cases.append(("NEG-M42-10", lambda d: d["m4-2-trusted-mutation-authorization-contract.json"]["freeze"].update(MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE="yes")))
    cases.append(("NEG-M42-11", lambda d: d["m4-2-capability-lease-issuance-contract.json"]["scope_boundary"].update(external_authority_write_implemented_in_m4_2="yes")))
    cases.append(("NEG-M42-12", lambda d: d["m4-2-m4-3-handoff.json"]["acceptance_gate"].update(M4_3_EXECUTION_AUTHORIZED="yes")))

    rejected = 0
    for name, mutator in cases:
        try:
            candidate = altered(mutator)
            assert_protocol(candidate)
        except (AssertionError, KeyError, TypeError, ValueError):
            rejected += 1
        else:
            raise AssertionError(f"negative case unexpectedly accepted: {name}")
    require(rejected == 12, f"negative rejection count: {rejected}")
    return rejected


def changed_paths() -> set[str]:
    commands = [
        ["git", "diff", "--name-only", EXPECTED_BASE, "HEAD"],
        ["git", "status", "--porcelain", "--untracked-files=all"],
    ]
    paths: set[str] = set()
    for command in commands:
        output = subprocess.check_output(command, cwd=ROOT, text=True)
        for line in output.splitlines():
            if command[1] == "status":
                value = line[3:] if len(line) >= 3 else ""
                if " -> " in value:
                    value = value.split(" -> ", 1)[1]
            else:
                value = line.strip()
            if value:
                paths.add(value)
    return paths


def assert_git_scope() -> tuple[str, str]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    require(branch == BRANCH, f"branch mismatch: {branch}")
    subprocess.check_call(["git", "merge-base", "--is-ancestor", EXPECTED_BASE, "HEAD"], cwd=ROOT)
    paths = changed_paths()
    for path in paths:
        require(path in ALLOWED_FILES or any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES), f"forbidden changed path: {path}")
        require(not path.startswith("aota_forge/"), f"functional core changed: {path}")
    return head, branch


def parse_all_json() -> int:
    count = 0
    for path in ROOT.rglob("*.json"):
        if ".git" in path.parts:
            continue
        with path.open(encoding="utf-8") as stream:
            json.load(stream)
        count += 1
    return count


def main() -> int:
    head, branch = assert_git_scope()
    docs = load_required()
    assert_protocol(docs)
    rejected = negative_cases(docs)
    parsed = parse_all_json()
    print(f"M4_2_ACTUAL_HEAD={head}")
    print(f"M4_2_BRANCH={branch}")
    print(f"M4_2_AUDIT_ENTRY_COUNT={len(docs['m4-2-current-authorization-capability-audit.json']['entries'])}")
    print(f"M4_2_JSON_PARSE_COUNT={parsed}")
    print("M4_2_NEGATIVE_CASE_COUNT=12")
    print(f"M4_2_NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print("M4_2_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT=0")
    print("M4_2_M4_4_SOURCE_PARALLEL_SAFETY=pending")
    print("M4_2_FUNCTIONAL_CORE_SOURCE_CHANGED=no")
    print("M4_2_PLAN_GUARD=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"M4_2_PLAN_GUARD=FAIL: {exc}")
        raise SystemExit(1)
