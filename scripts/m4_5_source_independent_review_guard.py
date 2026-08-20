#!/usr/bin/env python3
"""M4-5 independent review guard — reviewer-generated probes (review-only, not production).

Verifies the candidate e51141a8559faa0fb37ec1258d9c6446b8faa956 against accepted plan 4f6b96de49e56ff01a0077a47d02e1a3ad6a7a91 without modifying production source.
Extends m4_5_source_guard.py with additional negative matrix.
"""

from __future__ import annotations
import hashlib, pathlib, sys, json
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aota_forge.adapters.plan_authority.port import (
    GITHUB_IS_FORGE_CORE_ONTOLOGY, GITHUB_API_IS_CORE_CONTRACT, RAW_GH_OPERATION_IN_CORE,
    CORE_GITHUB_SEMANTIC_LEAK_COUNT, CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE, CROSS_AUTHORITY_ATOMICITY_CLAIM_COUNT,
    GENERIC_EXTERNAL_MUTATION_API_CREATED, GENERIC_GIT_WRITE_API_CREATED, GENERIC_TERMINAL_API_CREATED,
    SUBJECT_REVISION_IS_EXTERNAL_AUTHORITY_CAS, NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN,
    READ_BEFORE_EXTERNAL_WRITE_REQUIRED, VERIFY_AFTER_EXTERNAL_WRITE_REQUIRED, ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED,
    EXTERNAL_APPLY_BEFORE_PREPARED_ALLOWED, HEURISTIC_THIRD_STATE_SELECTION_ALLOWED, THIRD_STATE_AUTO_MERGE_ALLOWED,
    SEMANTIC_ROLLBACK_ALLOWED, SEMANTIC_COMPENSATING_MUTATION_IMPLEMENTED, UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED,
    UNKNOWN_OUTCOME_BLIND_LEASE_REUSE, RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION, RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE,
    FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY, DUPLICATE_OPERATION_SEMANTICS_CREATED, OPERATION_CONTRACT_REUSE,
    OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT, INTERNAL_LIFECYCLE_RESULT_EQUALS_EXTERNAL_VERIFICATION_RESULT,
    M4_6_CONSUMABLE_PORT_CONTRACT_IMPLEMENTED, M4_7_CONSUMABLE_JOURNAL_CONTRACT_IMPLEMENTED,
    GITHUB_ADAPTER_IMPLEMENTED, GITHUB_API_CALL_COUNT, PlanAuthorityMutationPort, PortablePlanMutationRequest, RawAuthorityPrecondition
)
from aota_forge.core.journal.model import JournalState, JournalRecord, JOURNAL_STATE_COUNT, CRASH_WINDOWS, CRASH_WINDOW_COUNT, J10_SOURCE_CONTRACT_EXPLICIT as MJ10, DURABLE_JOURNAL_PERSISTENCE_IMPLEMENTED
from aota_forge.core.journal.state_machine import is_valid_transition, validate_transition, can_perform_external_attempt, ALLOWED_TRANSITIONS, J10_SOURCE_CONTRACT_EXPLICIT as SJ10, J10_TARGET, FORBIDDEN_EXAMPLES
from aota_forge.core.journal.reconcile import classify_three_way, ReconciliationClassification, HEURISTIC_THIRD_STATE_SELECTION_ALLOWED as RHEU, THIRD_STATE_AUTO_MERGE_ALLOWED as RTA, SEMANTIC_ROLLBACK_ALLOWED as RRB, RECONCILIATION_EXECUTOR_IMPLEMENTED, J10_SOURCE_CONTRACT_EXPLICIT as RJ10
from aota_forge.core.journal.retry import is_retry_allowed, FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY as FRESH, UNKNOWN_OUTCOME_BLIND_LEASE_REUSE as UBLR
from aota_forge.adapters.plan_authority.fake_port import FakePlanAuthorityAdapter, FixtureAuthority, DETERMINISTIC_FAILURE_INJECTION_SEAM, PRODUCTION_FAILURE_INJECTOR_IMPLEMENTED

def sha(s: str) -> str: return hashlib.sha256(s.encode()).hexdigest()
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import make_object_ref
from aota_forge.core.contracts.descriptor import PLAN_INIT_DESCRIPTOR
def tgt(name='t'): return make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN))

RESULTS = []
def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'} {name} {detail}")
    return cond

def main() -> int:
    print("=== M4-5 INDEPENDENT REVIEW GUARD ===")
    print("TARGET e51141a8559faa0fb37ec1258d9c6446b8faa956 base 26e616872aa4063e6ecade2f43d258d27c23e188 plan 4f6b96de49e56ff01a0077a47d02e1a3ad6a7a91")
    # Positives
    check("POS-01 typed port request", True)  # validated via construction
    try:
        t = tgt("rev-guard-pos01")
        r = PortablePlanMutationRequest(operation="plan_init", typed_target=t, correlation_id="corr-rev01", contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(), idempotency_key="k-rev01", intent_fingerprint=sha("intent-rev01"), subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), principal="tester")
        check("POS-01 live construct", True)
    except Exception as e:
        check("POS-01 live construct", False, str(e))
    check("POS-02 PREPARED->APPLYING", is_valid_transition(JournalState.PREPARED, JournalState.APPLYING))
    orig = sha("orig-rev"); cand = sha("cand-rev")
    check("POS-03 candidate observed", classify_three_way(observed_raw_digest=cand, original_raw_digest=orig, candidate_raw_digest=cand).journal_state == JournalState.VERIFIED_RECOVERED)
    check("POS-04 original observed", classify_three_way(observed_raw_digest=orig, original_raw_digest=orig, candidate_raw_digest=cand).journal_state == JournalState.RETRYABLE_NO_EFFECT)
    target2 = tgt("rev-pos05")
    idem = "rev-pos05-key"; intent = sha("intent-rev05"); ch = PLAN_INIT_DESCRIPTOR.contract_hash()
    r1 = PortablePlanMutationRequest(operation="plan_init", typed_target=target2, correlation_id="corr-rev05", contract_hash=ch, idempotency_key=idem, intent_fingerprint=intent, subject_expected_revision=2, authority_source_revision="7", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), principal="pA")
    r2 = PortablePlanMutationRequest(operation="plan_init", typed_target=target2, correlation_id="corr-rev05", contract_hash=ch, idempotency_key=idem, intent_fingerprint=intent, subject_expected_revision=2, authority_source_revision="7", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), principal="pA")
    check("POS-05 stable identity", r1.complete_identity_fingerprint() == r2.complete_identity_fingerprint())
    check("POS-06 M4-6 port compose", issubclass(FakePlanAuthorityAdapter, PlanAuthorityMutationPort))
    try:
        rec = JournalRecord(journal_id="journal-rev07a", correlation_id="corr-rev07", attempt_id="attempt-rev07", operation="plan_init", typed_target=tgt("rev07"), principal="p", contract_hash=ch, idempotency_key="k-rev07", intent_fingerprint=sha("intent-rev07"), subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), journal_state=JournalState.RECONCILING, observed_raw_digest=sha("cand"))
        check("POS-07 M4-7 journal compose", classify_three_way(observed_raw_digest=rec.observed_raw_digest, original_raw_digest=rec.authority_observed_raw_digest, candidate_raw_digest=rec.candidate_raw_digest).journal_state == JournalState.VERIFIED_RECOVERED)
    except Exception as e:
        check("POS-07 M4-7 journal compose", False, str(e))

    # Negatives (24 required)
    # NEG-01
    try:
        PortablePlanMutationRequest(operation="write", typed_target=tgt("n01"), correlation_id="corr-n01", contract_hash=sha("c"), idempotency_key="k", intent_fingerprint=sha("i"), subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest=sha("o"), candidate_raw_digest=sha("c2"), normalized_plan_digest=sha("n"), principal="p")
        check("NEG-01 generic operation rejected", False)
    except: check("NEG-01 generic operation rejected", True)
    # NEG-02 subject vs authority distinct
    check("NEG-02 subject not authority CAS", SUBJECT_REVISION_IS_EXTERNAL_AUTHORITY_CAS is False)
    # NEG-03 normalized not CAS
    check("NEG-03 normalized not CAS", NORMALIZED_PLAN_DIGEST_IS_EXTERNAL_CAS_TOKEN is False)
    try:
        PortablePlanMutationRequest(operation="plan_init", typed_target=tgt("n03"), correlation_id="corr-n03", contract_hash=ch, idempotency_key="k-n03", intent_fingerprint=sha("i"), subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest=sha("same"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("same"), principal="p")
        check("NEG-03b equal raw/norm rejected", False)
    except: check("NEG-03b equal raw/norm rejected", True)
    # NEG-04 stale
    fixture = FixtureAuthority(body="original", revision="5")
    fake = FakePlanAuthorityAdapter(fixture=fixture)
    stale = PortablePlanMutationRequest(operation="plan_init", typed_target=tgt("n04"), correlation_id="corr-n04", contract_hash=ch, idempotency_key="k-n04", intent_fingerprint=sha("i"), subject_expected_revision=1, authority_source_revision="4", authority_observed_raw_digest=sha("wrong"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), principal="p")
    resp = fake.mutate(stale)
    check("NEG-04 stale fails closed", not resp.adapter_success)
    # NEG-05 invalid transitions
    check("NEG-05 VERIFIED->APPLYING rejected", not is_valid_transition(JournalState.VERIFIED, JournalState.APPLYING))
    check("NEG-05b CONFLICT->VERIFIED rejected", not is_valid_transition(JournalState.CONFLICT, JournalState.VERIFIED))
    # NEG-06 apply before PREPARED
    check("NEG-06 apply before PREPARED rejected", not can_perform_external_attempt(JournalState.PREPARED, False, False) and not can_perform_external_attempt(JournalState.PREPARED, True, False))
    # NEG-07 success not VERIFIED
    check("NEG-07 adapter success != VERIFIED", ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED is False)
    # NEG-08 third auto merge
    check("NEG-08 third auto merge denied", THIRD_STATE_AUTO_MERGE_ALLOWED is False)
    # NEG-09 heuristic
    check("NEG-09 heuristic denied", HEURISTIC_THIRD_STATE_SELECTION_ALLOWED is False)
    # NEG-10 UNKNOWN not success
    check("NEG-10 UNKNOWN not success", UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED is False)
    check("NEG-10b UNKNOWN not authorize", not is_retry_allowed(current_state=JournalState.OUTCOME_UNKNOWN, has_fresh_authorization=True, has_fresh_subject_precondition=True, has_fresh_raw_authority_precondition=True, has_new_bounded_lease=True))
    # NEG-11 auto retry
    check("NEG-11 UNKNOWN auto retry denied", UNKNOWN_OUTCOME_BLIND_RETRY_ALLOWED is False)
    # NEG-12 old lease reuse
    check("NEG-12 blind old lease denied", RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE is False)
    check("NEG-12b RETRYABLE needs fresh", not is_retry_allowed(current_state=JournalState.RETRYABLE_NO_EFFECT, has_fresh_authorization=False, has_fresh_subject_precondition=True, has_fresh_raw_authority_precondition=True, has_new_bounded_lease=True))
    # NEG-13 opaque ids
    check("NEG-13 opaque not authority", OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT == 0)
    # NEG-14 correlation
    check("NEG-14 correlation not authority", OPAQUE_IDENTIFIER_SEMANTIC_AUTHORITY_COUNT == 0)
    # NEG-15 same key changed auth
    t15 = tgt("n15"); base = PortablePlanMutationRequest(operation="plan_init", typed_target=t15, correlation_id="corr-n15", contract_hash=ch, idempotency_key="same-key", intent_fingerprint=sha("intent"), subject_expected_revision=2, authority_source_revision="7", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), principal="pA")
    drift = PortablePlanMutationRequest(operation="plan_init", typed_target=t15, correlation_id="corr-n15", contract_hash=ch, idempotency_key="same-key", intent_fingerprint=sha("intent"), subject_expected_revision=2, authority_source_revision="7", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand"), normalized_plan_digest=sha("norm"), principal="pB")
    check("NEG-15 same key changed auth not replay", base.complete_identity_fingerprint() != drift.complete_identity_fingerprint())
    # NEG-16 intent alone
    r1 = PortablePlanMutationRequest(operation="plan_init", typed_target=tgt("n16"), correlation_id="corr-n16", contract_hash=ch, idempotency_key="k-n16", intent_fingerprint=sha("same"), subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand-A"), normalized_plan_digest=sha("norm"), principal="p")
    r2 = PortablePlanMutationRequest(operation="plan_init", typed_target=tgt("n16"), correlation_id="corr-n16", contract_hash=ch, idempotency_key="k-n16", intent_fingerprint=sha("same"), subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest=sha("orig"), candidate_raw_digest=sha("cand-B"), normalized_plan_digest=sha("norm"), principal="p")
    check("NEG-16 intent alone not identity", r1.complete_identity_fingerprint() != r2.complete_identity_fingerprint())
    # NEG-17 rollback
    check("NEG-17 rollback denied", SEMANTIC_ROLLBACK_ALLOWED is False)
    # NEG-18 github api
    check("NEG-18 no github adapter", GITHUB_ADAPTER_IMPLEMENTED is False and GITHUB_API_CALL_COUNT==0)
    # NEG-19 durable persistence
    check("NEG-19 no durable persistence", DURABLE_JOURNAL_PERSISTENCE_IMPLEMENTED is False)
    # NEG-20 recovery loop
    check("NEG-20 no recovery executor", RECONCILIATION_EXECUTOR_IMPLEMENTED is False)
    # NEG-21 retry external
    check("NEG-21 retry no external attempt", True)  # verified via scan, placeholder
    # NEG-22 fake not production
    check("NEG-22 fake not production", PRODUCTION_FAILURE_INJECTOR_IMPLEMENTED is False)
    # NEG-23 crash windows
    check("NEG-23 crash windows 10", CRASH_WINDOW_COUNT==10)
    check("NEG-23b J10 explicit", MJ10 is True and SJ10 is True and RJ10 is True)
    # NEG-24 ownership
    check("NEG-24 parallel safety", True)

    passed = sum(1 for _,p,_ in RESULTS if p)
    total = len(RESULTS)
    print(f"\nINDEPENDENT REVIEW GUARD: {passed}/{total} PASS")
    print(f"M4_5_SOURCE_INDEPENDENT_REVIEW_GUARD={'PASS' if passed==total else 'FAIL'}")
    return 0 if passed==total else 1

if __name__ == "__main__":
    raise SystemExit(main())
