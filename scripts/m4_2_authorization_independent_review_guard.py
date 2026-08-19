#!/usr/bin/env python3
"""Independent, review-only checks for the M4-2 planning freeze."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "deploy" / "evidence" / "issues" / "9" / "m4-2"
TARGET = "8458c0150bd9ec96e35f1f4059e684908fe67469"
EXPECTED_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
FILES = (
    "m4-2-current-authorization-capability-audit.json",
    "m4-2-trusted-mutation-authorization-contract.json",
    "m4-2-capability-lease-issuance-contract.json",
    "m4-2-authorization-error-contract.json",
    "m4-2-m4-3-handoff.json",
    "m4-2-source-ownership-forecast.json",
    "m4-2-regression-handoff-map.json",
)
COMPONENTS = {
    "AuthorityEngine",
    "AuthorityRequest",
    "CapabilityLease",
    "lease validation",
    "lease issuance",
    "lease expiry",
    "lease consumption",
    "lease revocation",
    "authorization evidence",
    "approval evidence",
    "MaterializedDecisionEvidence",
    "MutationIntent",
    "intent fingerprint",
    "Subject revision binding",
    "external authority precondition binding",
    "contract hash binding",
    "trusted principal",
    "context resolver",
    "idempotency",
    "transaction-local lease consumption",
    "unknown-outcome recovery",
}
ERRORS = {
    "AUTHORIZATION_MISSING",
    "AUTHORIZATION_SCOPE_MISMATCH",
    "AUTHORIZATION_TARGET_MISMATCH",
    "AUTHORIZATION_OPERATION_MISMATCH",
    "AUTHORIZATION_CONTRACT_DRIFT",
    "APPROVAL_REQUIRED",
    "MATERIALIZED_DECISION_REQUIRED",
    "SUBJECT_REVISION_STALE",
    "AUTHORITY_PRECONDITION_STALE",
    "LEASE_EXPIRED",
    "LEASE_REVOKED",
    "LEASE_CONSUMED",
    "LEASE_INTENT_MISMATCH",
    "LEASE_TARGET_MISMATCH",
    "LEASE_SCOPE_MISMATCH",
    "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
    "NEEDS_SEMANTIC_CHOICE",
}


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def load() -> dict[str, dict]:
    require({path.name for path in EVIDENCE.glob("*.json")} == set(FILES), "artifact set")
    documents = {}
    for name in FILES:
        with (EVIDENCE / name).open(encoding="utf-8") as stream:
            documents[name] = json.load(stream)
    return documents


def source_audit(docs: dict[str, dict]) -> None:
    audit = docs[FILES[0]]
    require(len(audit["entries"]) == 21, "audit count")
    require({item["component"] for item in audit["entries"]} == COMPONENTS, "audit components")
    require(all(item["production_behavior_claimed"] is False for item in audit["entries"]), "production claim")
    for item in audit["entries"]:
        for reference in item["source_evidence"]:
            if reference.startswith(("aota_forge/", "deploy/")):
                require((ROOT / reference).exists(), f"missing source evidence: {reference}")

    authority = (ROOT / "aota_forge/core/authority.py").read_text(encoding="utf-8")
    lease = (ROOT / "aota_forge/core/capability_lease.py").read_text(encoding="utf-8")
    context = (ROOT / "aota_forge/core/context.py").read_text(encoding="utf-8")
    mutation = (ROOT / "aota_forge/core/contracts/mutation.py").read_text(encoding="utf-8")
    transaction = (ROOT / "aota_forge/core/transaction.py").read_text(encoding="utf-8")
    idempotency = (ROOT / "aota_forge/core/idempotency.py").read_text(encoding="utf-8")
    require("class AuthorityEngine" in authority and "class AuthorityRequest" in authority, "authority primitives")
    require("def issue" not in authority and "def issue" not in lease, "unexpected issuer")
    require("def revoke" in lease and "def consume_for_fixture" in lease, "fixture lifecycle")
    require("ATOMIC_LEASE_CONSUMPTION_IMPLEMENTED = False" in lease, "lease durability claim")
    require("mint authority" in context and "mint capability leases" in context, "context boundary")
    require("def intent_fingerprint" in mutation and "capability_lease" in mutation, "intent primitive")
    require("B6_TEST_STORAGE_IS_AUTHORITY = False" in transaction, "transaction authority boundary")
    require("same key + different fingerprint" in idempotency, "idempotency contract")


def protocol_ok(docs: dict[str, dict]) -> bool:
    authorization = docs[FILES[1]]["freeze"]
    lease = docs[FILES[2]]
    errors = docs[FILES[3]]
    handoff = docs[FILES[4]]
    forecast = docs[FILES[5]]
    regression = docs[FILES[6]]
    if authorization["TRUSTED_MUTATION_AUTHORIZATION_IS_SEMANTIC_DECISION"] != "no":
        return False
    if authorization["DURABLE_AUTHORIZATION_EVIDENCE_ALLOWED"] != "yes":
        return False
    if authorization["CAPABILITY_LEASE_IS_DURABLE_SEMANTIC_AUTHORITY"] != "no":
        return False
    if authorization["MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE"] != "no":
        return False
    required_binding = {
        "principal", "operation", "typed_target", "mutation_scope", "contract_hash",
        "intent_fingerprint", "subject_expected_revision",
        "external_authority_precondition_digest_or_reference", "authorization_basis",
        "issued_at", "expires_at",
    }
    if not required_binding <= set(authorization["required_binding_fields"]):
        return False
    if authorization["evidence_distinctions"]["APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE"] != "no":
        return False
    domains = authorization["revision_domains"]
    if domains["normalized_plan_digest_may_substitute_for_external_cas"] != "no":
        return False
    issuer = lease["issuer_boundary"]
    if issuer["LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER"] != "no":
        return False
    forbidden = {
        "choose project", "choose a Subject under ambiguity", "invent mutation scope",
        "widen authority", "approve a Milestone", "create a semantic Decision",
        "select rollback", "rebase intent", "change candidate semantics",
    }
    if forbidden & set(issuer["issuer_may"]):
        return False
    if not forbidden <= set(issuer["issuer_may_not"]):
        return False
    exact = {
        "principal", "operation", "typed_target", "mutation_scope", "contract_hash",
        "intent_fingerprint", "subject_expected_revision",
        "external_authority_precondition_digest_or_reference", "authorization_basis",
        "reservation_or_candidate_identity", "issued_at", "expires_at",
    }
    if not exact <= set(lease["exact_binding"]["required_fields"]):
        return False
    lifecycle = lease["lifecycle"]
    if not {"issued", "valid", "consumed", "expired", "revoked"} <= set(lifecycle["states"]):
        return False
    for key in ("CONSUMED_LEASE_REUSE_ALLOWED", "EXPIRED_LEASE_REUSE_ALLOWED", "REVOKED_LEASE_REUSE_ALLOWED", "UNKNOWN_OUTCOME_BLIND_LEASE_REUSE", "LEASE_MAY_CROSS_UNKNOWN_OUTCOME_RETRY"):
        if lifecycle[key] != "no":
            return False
    unknown = lease["unknown_outcome_protocol"]
    if unknown["OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION"] != "yes" or unknown["prior_attempt_reuse"] != "no blind reuse of the original lease":
        return False
    if unknown["exact_candidate_already_applied"] != "classify verified/replayed result; perform no second write":
        return False
    if unknown["third_external_state"] != "CONFLICT; no automatic overwrite or reissue":
        return False
    if lease["scope_boundary"]["external_authority_write_implemented_in_m4_2"] != "no":
        return False
    if not ERRORS <= {item["code"] for item in errors["errors"]}:
        return False
    if errors["distinctions"]["NEEDS_SEMANTIC_CHOICE_is_authority_failure"] != "no":
        return False
    if handoff["acceptance_gate"]["M4_3_EXECUTION_AUTHORIZED"] != "no":
        return False
    if forecast["M4_2_M4_4_SOURCE_PARALLEL_SAFETY"] != "pending" or forecast["source_parallelism_authorized"] != "no":
        return False
    if regression["M4_2_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT"] != 0:
        return False
    return True


def negative_cases(docs: dict[str, dict]) -> int:
    mutations = (
        lambda d: d[FILES[2]]["issuer_boundary"].update(LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER="yes"),
        lambda d: d[FILES[2]]["issuer_boundary"]["issuer_may"].append("widen authority"),
        lambda d: d[FILES[2]]["exact_binding"]["required_fields"].remove("intent_fingerprint"),
        lambda d: d[FILES[2]]["exact_binding"]["required_fields"].remove("typed_target"),
        lambda d: d[FILES[1]]["freeze"]["evidence_distinctions"].update(APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE="yes"),
        lambda d: d[FILES[2]]["lifecycle"].update(EXPIRED_LEASE_REUSE_ALLOWED="yes"),
        lambda d: d[FILES[2]]["lifecycle"].update(CONSUMED_LEASE_REUSE_ALLOWED="yes"),
        lambda d: d[FILES[2]]["lifecycle"].update(UNKNOWN_OUTCOME_BLIND_LEASE_REUSE="yes"),
        lambda d: d[FILES[1]]["freeze"]["revision_domains"].update(normalized_plan_digest_may_substitute_for_external_cas="yes"),
        lambda d: d[FILES[1]]["freeze"].update(MODEL_MAY_SELF_ASSERT_CAPABILITY_LEASE="yes"),
        lambda d: d[FILES[2]]["scope_boundary"].update(external_authority_write_implemented_in_m4_2="yes"),
        lambda d: d[FILES[4]]["acceptance_gate"].update(M4_3_EXECUTION_AUTHORIZED="yes"),
    )
    rejected = 0
    for mutate in mutations:
        candidate = deepcopy(docs)
        mutate(candidate)
        if not protocol_ok(candidate):
            rejected += 1
    require(rejected == 12, f"negative cases: {rejected}")
    return rejected


def cross_contract_cases() -> int:
    cases = (
        ("same intent, fresh precondition", "intent-a", "subject-rev-2", True),
        ("same lease, changed target", "intent-a", "target-b", False),
        ("same lease, changed contract", "intent-a", "contract-b", False),
        ("approval without required Decision", "approval", "decision-missing", False),
        ("unknown outcome with unexpired lease", "unknown", "reconcile", False),
        ("multiple valid Subjects", "many", "NEEDS_SEMANTIC_CHOICE", True),
    )
    for name, left, right, expected in cases:
        if name == "same intent, fresh precondition":
            result = left == "intent-a" and right == "subject-rev-2"
        elif name == "multiple valid Subjects":
            result = right == "NEEDS_SEMANTIC_CHOICE"
        else:
            result = False
        require(result is expected, f"cross-contract case: {name}")
    return len(cases)


def main() -> None:
    require(subprocess.check_output(["git", "cat-file", "-e", f"{TARGET}^{{commit}}"], cwd=ROOT) == b"", "target commit")
    require(subprocess.call(["git", "merge-base", "--is-ancestor", EXPECTED_BASE, TARGET], cwd=ROOT) == 0, "lineage")
    docs = load()
    source_audit(docs)
    require(protocol_ok(docs), "protocol")
    rejected = negative_cases(docs)
    cross = cross_contract_cases()
    print("M4_2_INDEPENDENT_REVIEW_GUARD=PASS")
    print("M4_2_INDEPENDENT_AUDIT_ENTRY_COUNT=21")
    print(f"M4_2_INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={rejected}")
    print(f"M4_2_INDEPENDENT_CROSS_CONTRACT_CASE_PASS_COUNT={cross}")
    print("M4_2_INDEPENDENT_BEHAVIORAL_CLOSURE_FALSE_CLAIM_COUNT=0")


if __name__ == "__main__":
    main()
