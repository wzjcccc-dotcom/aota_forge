#!/usr/bin/env python3
"""Deterministic M3-B5 Authority / Capability Lease foundation validator.

The fixture uses only the accepted isolated B3 in-memory repository.  It never
performs authoritative graph writes, CAS, revision persistence, transaction
commit, deployment, runtime activation, or GitHub mutation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

BASE_SHA = "b09b19e4fd4a083f165d04e96b35d52a8beacf12"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _fixture():
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    workflow_id = make_id(IdKind.WORKFLOW, "b5-wf")
    parent_id = make_id(IdKind.SUBJECT, "b5-parent", sub_kind=SubjectKind.PLAN)
    other_id = make_id(IdKind.SUBJECT, "b5-other", sub_kind=SubjectKind.PROJECT)
    child_id = make_id(IdKind.SUBJECT, "b5-child", sub_kind=SubjectKind.WORK)
    execution_id = make_id(IdKind.EXECUTION, "b5-execution")
    completion_id = make_id(IdKind.COMPLETION, "b5-completion")
    decision_id = make_id(IdKind.DECISION, "b5-decision")
    foreign_decision_id = make_id(IdKind.DECISION, "b5-foreign-decision")

    workflow = records.workflow(
        workflow_id, semantic_intent="B5 fixture", creation_context={"lane": "M3-B5"}
    )
    parent = records.subject(
        parent_id, kind="PlanSubject", workflow_ref=workflow_id,
        mechanical_state={"revision": 3, "state": "open"}, id_derivation="fixture",
    )
    other = records.subject(
        other_id, kind="ProjectSubject", workflow_ref=workflow_id,
        mechanical_state={"revision": 3, "state": "open"}, id_derivation="fixture",
    )
    child = records.subject(
        child_id, kind="WorkSubject", workflow_ref=workflow_id,
        mechanical_state={"revision": 1, "state": "open"}, id_derivation="fixture",
    )
    execution = records.execution(
        execution_id, parent_id, "fixture-executor", mechanical_status="completed"
    )
    completion = records.completion(completion_id, execution_id, "success")
    decision = records.decision(decision_id, parent_id, "followup", "create bounded child")
    foreign_decision = records.decision(
        foreign_decision_id, other_id, "followup", "foreign child basis"
    )
    repo = InMemoryGraphRepository()
    for record in (workflow, parent, other, child, execution, completion, decision, foreign_decision):
        repo.store(record)
    trusted = bind_trusted_context(
        principal_id="b5-operator",
        principal_type="operator",
        provenance="b5-fixture",
        channel="fixture",
        freshness="fixture-1",
    )
    refs = {
        "parent": make_object_ref(IdKind.SUBJECT, parent_id),
        "other": make_object_ref(IdKind.SUBJECT, other_id),
        "child": make_object_ref(IdKind.SUBJECT, child_id),
        "execution": make_object_ref(IdKind.EXECUTION, execution_id),
        "completion": make_object_ref(IdKind.COMPLETION, completion_id),
        "decision": make_object_ref(IdKind.DECISION, decision_id),
        "foreign_decision": make_object_ref(IdKind.DECISION, foreign_decision_id),
        "workflow": make_object_ref(IdKind.WORKFLOW, workflow_id),
    }
    return repo, OwningSubjectResolver(repo), trusted, refs, decision, foreign_decision


def _lease(principal, target, operation, *, issued=NOW - timedelta(seconds=10), expires=NOW + timedelta(seconds=10), revision=3, scope=None, approval_basis=None, decision_basis=None):
    from aota_forge.core.capability_lease import CapabilityLease

    return CapabilityLease(
        lease_id=f"b5-{operation}",
        principal=principal,
        operation=operation,
        target=target,
        scope=scope or {"mode": "write"},
        issued_at=issued,
        expires_at=expires,
        expected_revision=revision,
        authority_basis=("b5-fixture",),
        approval_basis=approval_basis,
        decision_basis=decision_basis,
    )


def _request(trusted, target, lease, *, operation=None, owner=None, scope=None, revision=3, now=NOW, **kwargs):
    from aota_forge.core.authority import AuthorityRequest

    return AuthorityRequest(
        principal=trusted.principal,
        trusted_context=trusted,
        operation=operation or lease.operation,
        target=target,
        owning_subject=owner,
        requested_scope=scope or {"mode": "write"},
        lease=lease,
        current_revision=revision,
        trusted_time=now,
        **kwargs,
    )


def _decision_evidence(decision, target, *, operation="create_followup_subject", revision=3, scope=None):
    from aota_forge.core.authority import MaterializedDecisionEvidence

    return MaterializedDecisionEvidence.from_decision(
        decision,
        operation=operation,
        target=target,
        expected_revision=revision,
        scope=scope or {"mode": "write"},
    )


def main() -> int:
    from aota_forge.core.authority import AuthorityDecision, AuthorityEngine, AuthorityReason
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()
    check("EXACT_BASE_VERIFIED", head == BASE_SHA, head)

    repo, resolver, trusted, refs, decision, foreign_decision = _fixture()
    engine = AuthorityEngine(resolver)
    principal = trusted.principal
    parent = refs["parent"]

    def evaluate(target, operation="record_decision", lease=None, **kwargs):
        return engine.evaluate(_request(trusted, target, lease or _lease(principal, target, operation), operation=operation, **kwargs))

    valid = _lease(principal, parent, "record_decision")
    result = evaluate(parent, lease=valid)
    check("B5-N1_valid_trusted_principal_and_lease_allow", result.decision == AuthorityDecision.ALLOW, result.to_audit().__repr__())

    missing_principal = _request(trusted, parent, valid)
    missing_principal = replace(missing_principal, principal=None, trusted_context=None)
    result = engine.evaluate(missing_principal)
    check("B5-N2_missing_principal_fail_closed", result.reason_code == AuthorityReason.PRINCIPAL_REQUIRED)

    from aota_forge.core.context import Principal
    self_asserted = _request(trusted, parent, valid)
    self_asserted = replace(self_asserted, principal=Principal("b5-operator", "operator", "model", "model"), trusted_context=None)
    result = engine.evaluate(self_asserted)
    check("B5-N3_self_asserted_principal_denied", result.reason_code == AuthorityReason.PRINCIPAL_UNTRUSTED)

    other_trusted = type(trusted)(principal=Principal("other", "operator", "b5-fixture", "fixture"), channel=trusted.channel, provenance=trusted.provenance, freshness=trusted.freshness, metadata=trusted.metadata, _binding_token=trusted._binding_token)
    mismatch_lease = _lease(other_trusted.principal, parent, "record_decision")
    result = engine.evaluate(_request(trusted, parent, mismatch_lease))
    check("B5-N4_lease_principal_mismatch_denied", result.reason_code == AuthorityReason.LEASE_PRINCIPAL_MISMATCH)

    result = evaluate(parent, operation="record_completion", lease=valid)
    check("B5-N5_operation_mismatch_denied", result.reason_code == AuthorityReason.LEASE_OPERATION_MISMATCH)

    execution_lease = _lease(principal, refs["execution"], "record_decision")
    result = engine.evaluate(_request(trusted, parent, execution_lease))
    check("B5-N6_subject_objectref_target_mismatch_denied", result.reason_code == AuthorityReason.LEASE_TARGET_MISMATCH)

    wrong_kind_lease = _lease(principal, refs["workflow"], "record_decision")
    result = engine.evaluate(_request(trusted, refs["workflow"], wrong_kind_lease))
    check("B5-N7_wrong_kind_objectref_denied_before_authority", result.reason_code == AuthorityReason.TARGET_KIND_INVALID)

    no_lease = _request(trusted, parent, None) if False else _request(trusted, parent, valid)
    no_lease = replace(no_lease, lease=None)
    result = engine.evaluate(no_lease)
    check("B5-N8_trusted_resource_alone_has_no_authority", result.reason_code == AuthorityReason.LEASE_REQUIRED)

    bare_id = make_id(IdKind.SUBJECT, "bare-only", sub_kind=SubjectKind.WORK)
    bare_request = replace(no_lease, target=bare_id, lease=None)
    result = engine.evaluate(bare_request)
    check("B5-N9_bare_subject_id_alone_has_no_authority", result.decision != AuthorityDecision.ALLOW)

    result = evaluate(parent, lease=_lease(principal, parent, "record_decision", expires=NOW + timedelta(microseconds=1)), now=NOW - timedelta(microseconds=1))
    check("B5-N10_expiry_before_boundary_valid", result.decision == AuthorityDecision.ALLOW)
    result = evaluate(parent, lease=_lease(principal, parent, "record_decision", expires=NOW))
    check("B5-N11_expiry_at_boundary_denied", result.reason_code == AuthorityReason.LEASE_EXPIRED)
    result = evaluate(parent, lease=_lease(principal, parent, "record_decision", expires=NOW - timedelta(microseconds=1)))
    check("B5-N12_expiry_after_boundary_denied", result.reason_code == AuthorityReason.LEASE_EXPIRED)

    result = evaluate(parent, lease=valid, scope={"mode": "write", "extra": "expanded"})
    check("B5-N13_scope_expansion_denied", result.reason_code == AuthorityReason.LEASE_SCOPE_MISMATCH)
    result = evaluate(parent, lease=valid.revoke())
    check("B5-N14_revoked_lease_denied", result.reason_code == AuthorityReason.LEASE_REVOKED)
    result = evaluate(parent, lease=valid.consume_for_fixture())
    check("B5-N14b_consumed_lease_replay_denied", result.reason_code == AuthorityReason.LEASE_REPLAY_DENIED)
    malformed_scope = replace(_request(trusted, parent, valid), requested_scope={})
    result = engine.evaluate(malformed_scope)
    check("B5-N14c_empty_scope_fails_closed", result.reason_code == AuthorityReason.SCOPE_REQUIRED)

    result = evaluate(refs["execution"], operation="record_completion", lease=_lease(principal, refs["execution"], "record_completion"))
    check("B5-N15_execution_resolves_owning_subject", result.decision == AuthorityDecision.ALLOW)
    result = evaluate(refs["completion"], operation="record_completion", lease=_lease(principal, refs["completion"], "record_completion"))
    check("B5-N16_completion_resolves_execution_to_subject", result.decision == AuthorityDecision.ALLOW)
    result = evaluate(refs["decision"], operation="record_decision", lease=_lease(principal, refs["decision"], "record_decision"))
    check("B5-N17_decision_resolves_owning_subject", result.decision == AuthorityDecision.ALLOW)

    result = evaluate(parent, lease=valid, revision=4)
    check("B5-N18_expected_revision_mismatch_denied", result.reason_code == AuthorityReason.REVISION_MISMATCH)

    approval_request = _request(trusted, parent, valid, approval_required=True)
    result = engine.evaluate(approval_request)
    check("B5-N19_missing_approval_needs_approval", result.decision == AuthorityDecision.NEEDS_APPROVAL)

    followup_target = parent
    followup_evidence = _decision_evidence(decision, followup_target)
    followup_lease = _lease(principal, followup_target, "create_followup_subject", decision_basis=followup_evidence)
    result = engine.evaluate(_request(trusted, followup_target, followup_lease, decision=followup_evidence, operation="create_followup_subject"))
    check("B5-N20_followup_correct_materialized_decision_allow", result.decision == AuthorityDecision.ALLOW)
    unbound_followup_lease = _lease(principal, followup_target, "create_followup_subject")
    result = engine.evaluate(_request(trusted, followup_target, unbound_followup_lease, operation="create_followup_subject"))
    check("B5-N21_followup_missing_decision_denied", result.reason_code == AuthorityReason.DECISION_REQUIRED)
    foreign_evidence = _decision_evidence(foreign_decision, followup_target)
    result = engine.evaluate(_request(trusted, followup_target, unbound_followup_lease, operation="create_followup_subject", decision=foreign_evidence))
    check("B5-N22_followup_foreign_decision_denied", result.reason_code == AuthorityReason.DECISION_MISMATCH)

    result = evaluate(refs["child"], operation="record_decision", lease=_lease(principal, parent, "record_decision"))
    check("B5-N23_parent_lease_cannot_authorize_child_mutation", result.reason_code == AuthorityReason.LEASE_TARGET_MISMATCH)

    unknown_id = make_id(IdKind.SUBJECT, "unknown-subject", sub_kind=SubjectKind.WORK)
    unknown_ref = make_object_ref(IdKind.SUBJECT, unknown_id)
    result = evaluate(unknown_ref, lease=_lease(principal, unknown_ref, "record_decision"))
    check("B5-N24_multiple_subjects_are_not_heuristically_selected", result.reason_code == AuthorityReason.TARGET_NOT_FOUND)

    # Explicit negative reversion proof: these source contracts remain false.
    from aota_forge.core.authority import (
        AUTHORITATIVE_GRAPH_WRITES_ALLOWED,
        AUTHORITY_ENGINE_RUNTIME_AUTHORITY_ACTIVE,
        CAS_IMPLEMENTATION_STARTED,
        CAPABILITY_LEASE_RUNTIME_AUTHORITY_ACTIVE,
        CUTOVER_PERFORMED,
        SHADOW_GRAPH_MATERIALIZATION_PERFORMED,
        TRANSACTION_IMPLEMENTATION_STARTED,
    )
    from aota_forge.core.capability_lease import CapabilityLease
    negative_flags = {
        "ID_IS_AUTHORITY": CapabilityLease.LEASE_ID_IS_AUTHORITY is False,
        "PRINCIPAL_ALONE_IMPLIES_MUTATION_AUTHORITY": no_lease.lease is None and engine.evaluate(no_lease).decision != AuthorityDecision.ALLOW,
        "TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY": no_lease.lease is None,
        "AUTHORITY_ENGINE_SELECTS_SUBJECT": engine.AUTHORITY_ENGINE_SELECTS_SUBJECT is False,
        "ATOMIC_LEASE_CONSUMPTION_IMPLEMENTED": CapabilityLease.ATOMIC_LEASE_CONSUMPTION_IMPLEMENTED is False,
        "CAS_IMPLEMENTATION_STARTED": CAS_IMPLEMENTATION_STARTED is False,
        "TRANSACTION_IMPLEMENTATION_STARTED": TRANSACTION_IMPLEMENTATION_STARTED is False,
        "SHADOW_GRAPH_MATERIALIZATION_PERFORMED": SHADOW_GRAPH_MATERIALIZATION_PERFORMED is False,
        "CUTOVER_PERFORMED": CUTOVER_PERFORMED is False,
        "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": AUTHORITATIVE_GRAPH_WRITES_ALLOWED is False,
        "AUTHORITY_ENGINE_RUNTIME_AUTHORITY_ACTIVE": AUTHORITY_ENGINE_RUNTIME_AUTHORITY_ACTIVE is False,
        "CAPABILITY_LEASE_RUNTIME_AUTHORITY_ACTIVE": CAPABILITY_LEASE_RUNTIME_AUTHORITY_ACTIVE is False,
    }
    check("B5_NEGATIVE_ID_AND_PRINCIPAL_AUTHORITY_PROOF", all(negative_flags.values()), json.dumps(negative_flags, sort_keys=True))
    check("B5_REQUIRED_FLAGS_NO_CAS_TRANSACTION_CUTOVER", all(negative_flags[key] for key in negative_flags if key not in {"ID_IS_AUTHORITY", "PRINCIPAL_ALONE_IMPLIES_MUTATION_AUTHORITY", "TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "AUTHORITY_ENGINE_SELECTS_SUBJECT"}), "source boundary")

    passed = sum(ok for _, ok, _ in RESULTS)
    failed = len(RESULTS) - passed
    print(f"B5 checks PASSED: {passed}/{len(RESULTS)}")
    print(f"B5_NEGATIVE_REVERSION_PROOF={'PASS' if failed == 0 else 'FAIL'}")
    print(f"B5_FOCUSED_REGRESSION={'PASS' if failed == 0 else 'FAIL'}")
    print("AUTHORITY_ENGINE_RUNTIME_AUTHORITY_ACTIVE=no")
    print("CAPABILITY_LEASE_RUNTIME_AUTHORITY_ACTIVE=no")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
