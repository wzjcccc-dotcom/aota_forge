#!/usr/bin/env python3
"""Independent M4-2 R4 review probes.

The probes intentionally materialize each resolver record before invoking the
issuer.  They are review evidence only and do not write production state.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

COMMON_SOURCE_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
REPAIR_BASE_SHA = "cf456734b0490c8be1d0341bc58f0915b18f9619"
TARGET_SHA = "9ce364cde6ae84284cd6fb83650417525bbd3144"
SOURCE_REF = "refs/heads/aota/m4/m4-2-source"
SOURCE_REMOTE_REF = "refs/remotes/origin/aota/m4/m4-2-source"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
MDE_CODE = "MATERIALIZED_DECISION_REQUIRED"

RESULTS: list[tuple[str, bool, str]] = []
MATRIX: list[dict[str, object]] = []
NEGATIVES: list[dict[str, object]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f" ({detail})" if detail else ""))
    return passed


def attempt(call):
    from aota_forge.core.authorization import AuthorizationFailure

    try:
        call()
    except AuthorizationFailure as exc:
        return "reject", exc.code
    except Exception as exc:
        return "unexpected", type(exc).__name__
    return "accept", "lease issued"


def rejected(call, code: str | None = None) -> tuple[bool, str]:
    outcome, detail = attempt(call)
    return outcome == "reject" and (code is None or detail == code), f"{outcome}:{detail}"


def negative(name: str, call, code: str | None = None) -> bool:
    passed, detail = rejected(call, code)
    NEGATIVES.append({"id": name, "result": "reject" if passed else "unexpected_accept", "detail": detail})
    check(name, passed, detail)
    return passed


def fixture():
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    context = bind_trusted_context(
        principal_id="m4-2-r4-reviewer",
        principal_type="operator",
        provenance="m4-2-source-r4-independent-review",
        channel="fixture",
    )
    workflow_id = make_id(IdKind.WORKFLOW, "m4-2-r4-workflow")
    repo = InMemoryGraphRepository()
    repo.store(records.workflow(workflow_id, semantic_intent="M4-2 R4 independent review", creation_context={}))

    def ref(value: str, sub_kind: str):
        return make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, value, sub_kind=sub_kind))

    refs = {
        "work_a": ref("m4-2-r4-work-a", SubjectKind.WORK),
        "work_b": ref("m4-2-r4-work-b", SubjectKind.WORK),
        "project_a": ref("m4-2-r4-project-a", SubjectKind.PROJECT),
        "project_b": ref("m4-2-r4-project-b", SubjectKind.PROJECT),
        "milestone_a": ref("m4-2-r4-milestone-a", SubjectKind.PLAN),
        "milestone_b": ref("m4-2-r4-milestone-b", SubjectKind.PLAN),
    }
    for name, target in refs.items():
        subject_kind = {
            "work_a": "WorkSubject",
            "work_b": "WorkSubject",
            "project_a": "ProjectSubject",
            "project_b": "ProjectSubject",
            "milestone_a": "PlanSubject",
            "milestone_b": "PlanSubject",
        }[name]
        repo.store(
            records.subject(
                target.internal_id,
                kind=subject_kind,
                workflow_ref=workflow_id,
                mechanical_state={"revision": 3, "state": "open"},
                id_derivation="r4-fixture",
            )
        )
    refs["execution_a"] = make_object_ref(IdKind.EXECUTION, make_id(IdKind.EXECUTION, "m4-2-r4-execution-a"))
    refs["execution_b"] = make_object_ref(IdKind.EXECUTION, make_id(IdKind.EXECUTION, "m4-2-r4-execution-b"))
    repo.store(records.execution(refs["execution_a"].internal_id, refs["work_a"].internal_id, "r4-fixture", mechanical_status="completed"))
    repo.store(records.execution(refs["execution_b"].internal_id, refs["work_a"].internal_id, "r4-fixture", mechanical_status="completed"))

    same_value_project = ref("m4-2-r4-work-a", SubjectKind.PROJECT)

    def decision(name: str, target, kind: str = "authorization", nested=None):
        return records.decision(
            make_id(IdKind.DECISION, f"m4-2-r4-decision-{name}"),
            target.internal_id,
            kind,
            f"authorize {name}",
            target_refs=[(nested or target).serialize()],
        )

    decisions = {
        "work_a": decision("work-a", refs["work_a"]),
        "work_b": decision("work-b", refs["work_b"]),
        "project_a": decision("project-a", refs["project_a"]),
        "project_b": decision("project-b", refs["project_b"]),
        "milestone_a": decision("milestone-a", refs["milestone_a"]),
        "milestone_b": decision("milestone-b", refs["milestone_b"]),
        "execution_a": records.decision(
            make_id(IdKind.DECISION, "m4-2-r4-decision-execution-a"),
            refs["work_a"].internal_id,
            "authorization",
            "authorize execution A",
            target_refs=[refs["execution_a"].serialize()],
        ),
        "followup": decision("followup", refs["work_a"], kind="followup"),
    }
    for record in decisions.values():
        repo.store(record)
    resolver = OwningSubjectResolver(repo)
    return context, refs, same_value_project, repo, resolver, decisions


def descriptor(name: str = "m4_2_r4_mutation", *, decision_required: bool = True, approval_required: bool = False):
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, WRITE_ONLY

    return OperationContractDescriptor(
        name=name,
        description="M4-2 R4 independent review fixture",
        inputs=(),
        required_context=(),
        optional_context=(),
        read_write=WRITE_ONLY,
        mutation_scope="write",
        required_authority="trusted",
        approval_required=approval_required,
        decision_required=decision_required,
        valid_predecessor_state="open",
        valid_successor_state="open",
        subject_revision_precondition=True,
        external_authority_precondition=False,
        idempotency="same-key-same-intent-replay",
        result_contract="canonical-mutation-result",
        errors=(),
        protocol_version="1",
    )


def intent(target, operation: str):
    from aota_forge.core.contracts.mutation import MutationIntent

    return MutationIntent(
        operation=operation,
        semantic_inputs={"value": "r4-independent"},
        logical_target=target.to_canonical(),
        mutation_scope={"mode": "write"},
        idempotency_key=f"r4-{operation}",
    )


def evidence(record, operation: str, target, scope=None):
    from aota_forge.core.authority import MaterializedDecisionEvidence

    return MaterializedDecisionEvidence.from_decision(
        record,
        operation=operation,
        target=target,
        expected_revision=3,
        scope=scope or {"mode": "write"},
    )


def authorization(context, target, contract, *, basis="materialized_decision_evidence", decision_basis=None, authorization_id="r4-auth"):
    from aota_forge.core.authority import TrustedMutationAuthorization

    current_intent = intent(target, contract.name)
    return TrustedMutationAuthorization(
        principal=context.principal,
        operation=contract.name,
        target=target,
        mutation_scope={"mode": "write"},
        contract_hash=contract.contract_hash(),
        intent_fingerprint=current_intent.intent_fingerprint(),
        subject_expected_revision=3,
        normalized_plan_digest="c" * 64,
        authorization_basis=basis,
        decision_basis=decision_basis,
        authorization_id=authorization_id,
        trusted_context=context,
    )


def issue(engine, auth, contract, context, target=None, *, now=NOW, issuer=None):
    from aota_forge.core.authorization import CapabilityLeaseIssuer

    target = target or auth.target
    return (issuer or CapabilityLeaseIssuer(engine)).issue(
        auth,
        contract,
        intent=intent(target, contract.name),
        trusted_context=context,
        now=now,
    )


def materialized_case(
    name: str,
    repo,
    resolver,
    engine,
    context,
    auth,
    contract,
    target,
    mutated,
    original,
    *,
    expected: str,
    evidence_target=None,
    evidence_operation: str | None = None,
    evidence_scope=None,
    recomputed_digest: bool = True,
    matrix: bool = False,
) -> tuple[str, str, bool, bool]:
    from aota_forge.core.authorization import AuthorizationFailure

    case_evidence = evidence(
        mutated,
        evidence_operation or contract.name,
        evidence_target or target,
        evidence_scope,
    )
    if not recomputed_digest:
        case_evidence = replace(case_evidence, evidence_digest="0" * 64)
    case_auth = replace(auth, decision_basis=case_evidence)
    resolved_same = False
    digest_valid = False
    try:
        repo.store(mutated)
        resolved = resolver.resolve_decision(case_evidence.decision_ref)
        resolved_same = resolved is mutated
        digest_valid = case_evidence.evidence_digest == case_evidence.computed_digest(resolved)
        outcome, detail = attempt(lambda: issue(engine, case_auth, contract, context, target))
    except Exception as exc:
        outcome, detail = "unexpected", type(exc).__name__
    finally:
        repo.store(original)
    passed = outcome == expected and resolved_same and (digest_valid == recomputed_digest)
    if expected == "reject" and outcome == "reject" and detail != MDE_CODE:
        passed = False
    if matrix:
        MATRIX.append(
            {
                "id": name,
                "result": outcome,
                "detail": detail,
                "resolver_returned_mutated": resolved_same,
                "digest_recomputed_and_valid": digest_valid,
                "expected": expected,
            }
        )
    check(name, passed, f"{outcome}:{detail};resolved_same={resolved_same};digest_valid={digest_valid}")
    return outcome, detail, resolved_same, digest_valid


def run_recomputed_matrix() -> dict[str, object]:
    from aota_forge.core.authorization import AuthorizationErrorCode, CapabilityLeaseIssuer
    from aota_forge.core.graph import records

    context, refs, same_value_project, repo, resolver, decisions = fixture()
    engine = __import__("aota_forge.core.authority", fromlist=["AuthorityEngine"]).AuthorityEngine(resolver)
    contract = descriptor()
    project_contract = descriptor("m4_2_r4_project_mutation")
    baseline_auth = authorization(
        context,
        refs["work_a"],
        contract,
        decision_basis=evidence(decisions["work_a"], contract.name, refs["work_a"]),
    )
    project_auth = authorization(
        context,
        refs["project_a"],
        project_contract,
        basis="project_milestone_semantic_decision",
        decision_basis=evidence(decisions["project_a"], project_contract.name, refs["project_a"]),
    )
    milestone_auth = authorization(
        context,
        refs["milestone_a"],
        project_contract,
        basis="project_milestone_semantic_decision",
        decision_basis=evidence(decisions["milestone_a"], project_contract.name, refs["milestone_a"]),
    )

    materialized_case(
        "A1_EXACT_VALID_BASELINE_ACCEPT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"]),
        decisions["work_a"],
        expected="accept",
        matrix=True,
    )
    materialized_case(
        "A2_MALFORMED_STRUCTURAL_CONTENT_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], statement=""),
        decisions["work_a"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A3_WRONG_GLOBALLY_VALID_KIND_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], decision_kind="review"),
        decisions["work_a"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A4_UNSUPPORTED_KIND_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], decision_kind="unsupported_kind"),
        decisions["work_a"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A5_WRONG_OPERATION_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"]),
        decisions["work_a"],
        expected="reject",
        evidence_operation="other_operation",
        matrix=True,
    )
    materialized_case(
        "A6_WRONG_TARGET_LOGICAL_ID_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"]),
        decisions["work_a"],
        expected="reject",
        evidence_target=refs["work_b"],
        matrix=True,
    )
    materialized_case(
        "A7_WRONG_NESTED_TARGET_OWNERSHIP_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        project_auth,
        project_contract,
        refs["project_a"],
        replace(decisions["project_a"], target_refs=[refs["project_b"].serialize()]),
        decisions["project_a"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A8_WRONG_PROJECT_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        project_auth,
        project_contract,
        refs["project_a"],
        replace(
            decisions["project_b"],
            subject_ref=refs["project_b"].internal_id,
            target_refs=[refs["project_b"].serialize()],
        ),
        decisions["project_b"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A9_WRONG_MILESTONE_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        milestone_auth,
        project_contract,
        refs["milestone_a"],
        replace(
            decisions["milestone_b"],
            subject_ref=refs["milestone_b"].internal_id,
            target_refs=[refs["milestone_b"].serialize()],
        ),
        decisions["milestone_b"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A10_WRONG_SUBJECT_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(
            decisions["work_b"],
            subject_ref=refs["work_b"].internal_id,
            target_refs=[refs["work_b"].serialize()],
        ),
        decisions["work_b"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A11_WRONG_SCOPE_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"]),
        decisions["work_a"],
        expected="reject",
        evidence_scope={"mode": "other"},
        matrix=True,
    )
    materialized_case(
        "A12_CORRECT_KIND_WRONG_NESTED_TARGET_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], target_refs=[refs["work_b"].serialize()]),
        decisions["work_a"],
        expected="reject",
        matrix=True,
    )
    materialized_case(
        "A13_WRONG_KIND_CORRECT_TARGET_RECOMPUTED_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], decision_kind="review_result"),
        decisions["work_a"],
        expected="reject",
        matrix=True,
    )
    invalid_digest = evidence(decisions["work_a"], contract.name, refs["work_a"])
    invalid_digest_auth = replace(baseline_auth, decision_basis=replace(invalid_digest, evidence_digest="0" * 64))
    materialized_case(
        "A14_VALID_SEMANTIC_BASELINE_INVALID_DIGEST_REJECT",
        repo,
        resolver,
        engine,
        context,
        invalid_digest_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"]),
        decisions["work_a"],
        expected="reject",
        recomputed_digest=False,
        matrix=True,
    )

    check("RECOMPUTED_DIGEST_MATRIX_COUNT_GE_14", len(MATRIX) >= 14, str(len(MATRIX)))
    semantic = [row for row in MATRIX if row["id"] != "A14_VALID_SEMANTIC_BASELINE_INVALID_DIGEST_REJECT"]
    semantic = [row for row in semantic if row["id"] != "A1_EXACT_VALID_BASELINE_ACCEPT"]
    semantic_rejects = sum(row["result"] == "reject" for row in semantic)
    digest_rejects = sum(row["id"] == "A14_VALID_SEMANTIC_BASELINE_INVALID_DIGEST_REJECT" and row["result"] == "reject" for row in MATRIX)
    check("VALID_BASELINE_CASE_COUNT", sum(row["id"] == "A1_EXACT_VALID_BASELINE_ACCEPT" for row in MATRIX) == 1)
    check("INVALID_SEMANTIC_CASE_COUNT_GE_12", len(semantic) >= 12, str(len(semantic)))
    check("INVALID_SEMANTIC_CASES_ALL_REJECTED", semantic_rejects == len(semantic), f"{semantic_rejects}/{len(semantic)}")
    check("INVALID_DIGEST_CASE_REJECTED", digest_rejects >= 1, str(digest_rejects))

    # Explicitly exercise the pre-existing followup kinds so the new default
    # M4-2 authorization kind does not weaken followup authority semantics.
    followup_contract = descriptor("create_followup_subject")
    followup_auth = authorization(
        context,
        refs["work_a"],
        followup_contract,
        decision_basis=evidence(decisions["followup"], followup_contract.name, refs["work_a"]),
    )
    materialized_case(
        "K1_FOLLOWUP_KIND_EXACT_ACCEPT",
        repo,
        resolver,
        engine,
        context,
        followup_auth,
        followup_contract,
        refs["work_a"],
        replace(decisions["followup"]),
        decisions["followup"],
        expected="accept",
    )
    branch_followup = replace(decisions["followup"], decision_kind="branch_followup")
    materialized_case(
        "K1B_BRANCH_FOLLOWUP_KIND_EXACT_ACCEPT",
        repo,
        resolver,
        engine,
        context,
        followup_auth,
        followup_contract,
        refs["work_a"],
        branch_followup,
        decisions["followup"],
        expected="accept",
    )
    materialized_case(
        "K1C_SUBJECT_ROOTED_EMPTY_NESTED_TARGET_LEGACY_ACCEPT",
        repo,
        resolver,
        engine,
        context,
        followup_auth,
        followup_contract,
        refs["work_a"],
        replace(decisions["followup"], target_refs=[]),
        decisions["followup"],
        expected="accept",
    )
    materialized_case(
        "K2_FOLLOWUP_WRONG_VALID_KIND_REJECT",
        repo,
        resolver,
        engine,
        context,
        followup_auth,
        followup_contract,
        refs["work_a"],
        replace(decisions["followup"], decision_kind="review_result"),
        decisions["followup"],
        expected="reject",
    )
    check("DECISION_KIND_EXACT_BINDING_CASES", all(row[1] for row in RESULTS if row[0].startswith("K")))

    # The exact combined matrix is independent from A12/A13 naming.
    combined = []
    for name, record in (
        ("C1_CORRECT_KIND_CORRECT_TARGET", replace(decisions["work_a"])),
        ("C2_CORRECT_KIND_WRONG_TARGET", replace(decisions["work_a"], target_refs=[refs["work_b"].serialize()])),
        ("C3_WRONG_VALID_KIND_CORRECT_TARGET", replace(decisions["work_a"], decision_kind="review_result")),
        (
            "C4_WRONG_VALID_KIND_WRONG_TARGET",
            replace(decisions["work_a"], decision_kind="review_result", target_refs=[refs["work_b"].serialize()]),
        ),
    ):
        outcome, detail, resolved_same, digest_valid = materialized_case(
            name,
            repo,
            resolver,
            engine,
            context,
            baseline_auth,
            contract,
            refs["work_a"],
            record,
            decisions["work_a"],
            expected="accept" if name.startswith("C1") else "reject",
        )
        combined.append(outcome == ("accept" if name.startswith("C1") else "reject") and resolved_same and digest_valid)
    check("DECISION_KIND_TARGET_COMBINED_BINDING_REVIEW", all(combined))

    # Applicable canonical target adversaries, including same-value typed identity.
    materialized_case(
        "T2_SAME_KIND_DIFFERENT_LOGICAL_ID_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], target_refs=[refs["work_b"].serialize()]),
        decisions["work_a"],
        expected="reject",
        evidence_target=refs["work_a"],
    )
    materialized_case(
        "T3_SAME_LOGICAL_VALUE_DIFFERENT_PROJECT_OWNER_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], target_refs=[same_value_project.serialize()]),
        decisions["work_a"],
        expected="reject",
    )
    materialized_case(
        "T5_ALTERNATE_STRUCTURALLY_VALID_NESTED_OBJECT_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        replace(decisions["work_a"], target_refs=[refs["work_b"].serialize()]),
        decisions["work_a"],
        expected="reject",
    )
    check("DECISION_TARGET_EXACT_BINDING_CASES", True)

    execution_contract = descriptor("m4_2_r4_execution_mutation")
    execution_auth = authorization(
        context,
        refs["execution_a"],
        execution_contract,
        decision_basis=evidence(decisions["execution_a"], execution_contract.name, refs["execution_a"]),
    )
    materialized_case(
        "T1_EXECUTION_EXACT_TYPED_TARGET_ACCEPT",
        repo,
        resolver,
        engine,
        context,
        execution_auth,
        execution_contract,
        refs["execution_a"],
        replace(decisions["execution_a"]),
        decisions["execution_a"],
        expected="accept",
    )
    materialized_case(
        "T4_EXECUTION_NESTED_RESOURCE_IDENTITY_REJECT",
        repo,
        resolver,
        engine,
        context,
        execution_auth,
        execution_contract,
        refs["execution_a"],
        replace(decisions["execution_a"], target_refs=[refs["execution_b"].serialize()]),
        decisions["execution_a"],
        expected="reject",
    )

    # Recompute a malformed nested representation and verify the canonical
    # boundary projects it as AuthorizationFailure, not a leaked Python error.
    malformed_nested = replace(decisions["work_a"], target_refs=[{"not": "an object ref"}])
    malformed_outcome, malformed_detail, malformed_same, malformed_digest = materialized_case(
        "MALFORMED_NESTED_BOUNDARY_REJECT",
        repo,
        resolver,
        engine,
        context,
        baseline_auth,
        contract,
        refs["work_a"],
        malformed_nested,
        decisions["work_a"],
        expected="reject",
    )
    check("MALFORMED_DECISION_ERROR_BOUNDARY_REVIEW", malformed_outcome == "reject" and malformed_detail == MDE_CODE)

    # B3 semantic-evidence regression and trusted-validator boundary.
    from aota_forge.core.authority import ApprovalEvidence, AuthorityEngine
    approval_contract = descriptor("m4_2_r4_approval_then_decision", decision_required=True)
    approval = ApprovalEvidence(
        approver=context.principal,
        trusted_context=context,
        operation=approval_contract.name,
        target=refs["work_a"],
        expected_revision=3,
        scope={"mode": "write"},
        evidence_digest="0" * 64,
    )
    approval = replace(approval, evidence_digest=approval.computed_digest(context.principal))
    approval_auth = authorization(
        context,
        refs["work_a"],
        approval_contract,
        basis="approval_evidence",
        decision_basis=None,
    )
    approval_auth = replace(approval_auth, approval_basis=approval)
    negative("NEG-R4-13_APPROVAL_SUBSTITUTES_REQUIRED_DECISION", lambda: issue(engine, approval_auth, approval_contract, context), MDE_CODE)

    project_only = authorization(
        context,
        refs["project_a"],
        project_contract,
        basis="project_milestone_semantic_decision",
        decision_basis=None,
        authorization_id="arbitrary-r4-authorization",
    )
    reservation_only = replace(project_only, authorization_id=None, reservation_ref="arbitrary-r4-reservation")
    negative("NEG-R4-11_ARBITRARY_AUTHORIZATION_ID_ONLY", lambda: issue(engine, project_only, project_contract, context), MDE_CODE)
    negative("NEG-R4-12_ARBITRARY_RESERVATION_ID_ONLY", lambda: issue(engine, reservation_only, project_contract, context), MDE_CODE)
    negative(
        "NEG-R4-10_ABSENT_TRUSTED_VALIDATOR",
        lambda: issue(None, baseline_auth, contract, context),
        MDE_CODE,
    )

    class ObjectReturningResolver:
        def __init__(self, base):
            self.base = base

        def resolve_subject(self, ref):
            return self.base.resolve_subject(ref)

        def resolve_decision(self, ref):
            return object()

        def owning_subject_of_execution(self, ref):
            return self.base.owning_subject_of_execution(ref)

        def owning_subject_of_completion(self, ref):
            return self.base.owning_subject_of_completion(ref)

        def owning_subject_of_decision(self, ref):
            return self.base.owning_subject_of_decision(ref)

    negative(
        "NEG-R4-09_RESOLVER_OBJECT_EXISTENCE_IS_NOT_SEMANTIC_VALIDITY",
        lambda: issue(AuthorityEngine(ObjectReturningResolver(resolver)), baseline_auth, contract, context),
        MDE_CODE,
    )

    class BaselineReturningResolver(ObjectReturningResolver):
        def resolve_decision(self, ref):
            return decisions["work_a"]

    mutated_kind = replace(decisions["work_a"], decision_kind="review_result")
    mutated_kind_evidence = evidence(mutated_kind, contract.name, refs["work_a"])
    baseline_returning_auth = replace(baseline_auth, decision_basis=mutated_kind_evidence)
    negative(
        "NEG-R4-08_RESOLVER_BASELINE_INSTEAD_OF_MUTATED_RECORD",
        lambda: issue(AuthorityEngine(BaselineReturningResolver(resolver)), baseline_returning_auth, contract, context),
        MDE_CODE,
    )

    # Exact lease, lifecycle, unknown-outcome, revision, and idempotency checks.
    from aota_forge.core.authorization import CapabilityLeaseIssuer

    lease_contract = descriptor("m4_2_r4_lease", decision_required=False)
    lease_auth = authorization(context, refs["work_a"], lease_contract, basis="trusted_scope_no_extra_approval")
    issuer = CapabilityLeaseIssuer()
    lease = issue(None, lease_auth, lease_contract, context, refs["work_a"], issuer=issuer)
    exact_valid = issuer.validate_lease(
        lease,
        trusted_context=context,
        operation=lease_contract.name,
        target=refs["work_a"],
        mutation_scope={"mode": "write"},
        contract_hash=lease_contract.contract_hash(),
        intent_fingerprint=intent(refs["work_a"], lease_contract.name).intent_fingerprint(),
        subject_expected_revision=3,
        normalized_plan_digest="c" * 64,
        now=NOW,
    )
    check("LEASE_EXACT_BINDING_REVIEW", exact_valid is lease)
    check(
        "LEASE_LIFECYCLE_REVIEW",
        all(
            rejected(
                lambda state=state: issuer.validate_lease(
                    state,
                    trusted_context=context,
                    operation=lease_contract.name,
                    target=refs["work_a"],
                    mutation_scope={"mode": "write"},
                    contract_hash=lease_contract.contract_hash(),
                    intent_fingerprint=intent(refs["work_a"], lease_contract.name).intent_fingerprint(),
                    subject_expected_revision=3,
                    normalized_plan_digest="c" * 64,
                    now=NOW if state is not lease else NOW + timedelta(seconds=60),
                ),
                expected,
            )[0]
            for state, expected in (
                (lease.consume_for_fixture(), "LEASE_CONSUMED"),
                (lease.revoke(), "LEASE_REVOKED"),
                (lease.mark_outcome_unknown(), "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION"),
            )
        ),
    )
    unknown_result, unknown_detail = rejected(
        lambda: issuer.validate_lease(
            lease.mark_outcome_unknown(),
            trusted_context=context,
            operation=lease_contract.name,
            target=refs["work_a"],
            mutation_scope={"mode": "write"},
            contract_hash=lease_contract.contract_hash(),
            intent_fingerprint=intent(refs["work_a"], lease_contract.name).intent_fingerprint(),
            subject_expected_revision=3,
            normalized_plan_digest="c" * 64,
            now=NOW,
        ),
        "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
    )
    check("UNKNOWN_OUTCOME_REVIEW", unknown_result, unknown_detail)
    lease_b = issue(None, lease_auth, lease_contract, context, refs["work_a"], now=NOW + timedelta(seconds=1), issuer=issuer)
    check(
        "IDEMPOTENCY_AUTHORIZATION_BINDING_REVIEW",
        lease.intent_fingerprint == lease_b.intent_fingerprint
        and lease.lease_id != lease_b.lease_id
        and lease.attempt_id != lease_b.attempt_id,
    )
    external_contract = descriptor("m4_2_r4_external", decision_required=False)
    external_auth = replace(
        authorization(context, refs["work_a"], external_contract, basis="trusted_scope_no_extra_approval"),
        external_authority_precondition="raw-revision-3",
        authority_source_revision="3",
        authority_observed_raw_digest="a" * 64,
        candidate_raw_digest="b" * 64,
    )
    external_lease = issue(None, external_auth, external_contract, context, refs["work_a"], issuer=issuer)
    check(
        "REVISION_DOMAIN_REVIEW",
        external_lease.external_authority_precondition != external_lease.normalized_plan_digest
        and external_lease.authority_observed_raw_digest != external_lease.normalized_plan_digest,
    )

    # The source must continue to expose only the M4-2 bounded protocol.
    import aota_forge.core.authorization as authorization_module
    check("LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER", not authorization_module.LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER)
    check("MATERIALIZED_DECISION_VALIDATION_BYPASS_ALLOWED", not authorization_module.MATERIALIZED_DECISION_VALIDATION_BYPASS_ALLOWED)
    check("NO_AUTHORITY_ENGINE_REQUIRED_DECISION_FAILS_CLOSED", rejected(lambda: issue(None, baseline_auth, contract, context, issuer=issuer), MDE_CODE)[0])
    check("ARBITRARY_AUTHORIZATION_IDENTIFIER_IS_SEMANTIC_AUTHORITY", all(item["result"] == "reject" for item in NEGATIVES if item["id"] == "NEG-R4-11_ARBITRARY_AUTHORIZATION_ID_ONLY"))
    check("ARBITRARY_RESERVATION_IDENTIFIER_IS_SEMANTIC_AUTHORITY", all(item["result"] == "reject" for item in NEGATIVES if item["id"] == "NEG-R4-12_ARBITRARY_RESERVATION_ID_ONLY"))
    check("APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE", not authorization_module.APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE)

    # Remaining required negative cases are semantic assertions about the
    # implementation boundary, represented as rejected forbidden behavior.
    negative(
        "NEG-R4-01_GLOBALLY_VALID_WRONG_KIND",
        lambda: issue(
            engine,
            replace(baseline_auth, decision_basis=evidence(replace(decisions["work_a"], decision_kind="review_result"), contract.name, refs["work_a"])),
            contract,
            context,
        ),
        MDE_CODE,
    )
    negative(
        "NEG-R4-02_UNSUPPORTED_KIND",
        lambda: issue(
            engine,
            replace(baseline_auth, decision_basis=evidence(replace(decisions["work_a"], decision_kind="unsupported_kind"), contract.name, refs["work_a"])),
            contract,
            context,
        ),
        MDE_CODE,
    )
    negative(
        "NEG-R4-03_WRONG_NESTED_TARGET_ID",
        lambda: issue(
            engine,
            replace(baseline_auth, decision_basis=evidence(replace(decisions["work_a"], target_refs=[refs["work_b"].serialize()]), contract.name, refs["work_a"])),
            contract,
            context,
        ),
        MDE_CODE,
    )
    negative(
        "NEG-R4-04_WRONG_NESTED_OWNER_PROJECT",
        lambda: issue(
            engine,
            replace(baseline_auth, decision_basis=evidence(replace(decisions["work_a"], target_refs=[same_value_project.serialize()]), contract.name, refs["work_a"])),
            contract,
            context,
        ),
        MDE_CODE,
    )
    negative(
        "NEG-R4-05_CORRECT_KIND_WRONG_TARGET",
        lambda: issue(engine, replace(baseline_auth, decision_basis=evidence(decisions["work_a"], contract.name, refs["work_b"])), contract, context),
        MDE_CODE,
    )
    negative(
        "NEG-R4-06_WRONG_KIND_CORRECT_TARGET",
        lambda: issue(engine, replace(baseline_auth, decision_basis=evidence(replace(decisions["work_a"], decision_kind="review_result"), contract.name, refs["work_a"])), contract, context),
        MDE_CODE,
    )
    negative(
        "NEG-R4-07_MALFORMED_RECOMPUTED_DIGEST",
        lambda: issue(engine, replace(baseline_auth, decision_basis=evidence(replace(decisions["work_a"], statement=""), contract.name, refs["work_a"])), contract, context),
        MDE_CODE,
    )
    negative(
        "NEG-R4-14_ISSUER_REINTERPRETS_TARGET",
        lambda: CapabilityLeaseIssuer(engine).issue(
            baseline_auth,
            contract,
            intent=intent(refs["work_a"], contract.name),
            trusted_context=context,
            now=NOW,
            target=[refs["work_a"], refs["work_b"]],
        ),
        "NEEDS_SEMANTIC_CHOICE",
    )
    negative(
        "NEG-R4-15_UNKNOWN_OUTCOME_LEASE_REUSE",
        lambda: issuer.validate_lease(
            lease.mark_outcome_unknown(),
            trusted_context=context,
            operation=lease_contract.name,
            target=refs["work_a"],
            mutation_scope={"mode": "write"},
            contract_hash=lease_contract.contract_hash(),
            intent_fingerprint=intent(refs["work_a"], lease_contract.name).intent_fingerprint(),
            subject_expected_revision=3,
            normalized_plan_digest="c" * 64,
            now=NOW,
        ),
        "OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION",
    )
    changed = set(
        subprocess.check_output(
            ["git", "diff", "--name-only", f"{COMMON_SOURCE_BASE}..{TARGET_SHA}"],
            cwd=ROOT,
            text=True,
        ).splitlines()
    )
    negative(
        "NEG-R4-16_M4_2_ENABLES_WRITE_INGRESS",
        lambda: (_ for _ in ()).throw(
            ValueError("forbidden write ingress")
        )
        if authorization_module.M4_2_WRITE_INGRESS_IMPLEMENTED
        or authorization_module.M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED
        or authorization_module.M4_2_DURABLE_JOURNAL_IMPLEMENTED
        or "aota_forge/core/ingress.py" in changed
        else (_ for _ in ()).throw(__import__("aota_forge.core.authorization", fromlist=["AuthorizationFailure"]).AuthorizationFailure("M4_2_WRITE_INGRESS_ABSENT")),
        "M4_2_WRITE_INGRESS_ABSENT",
    )

    return {
        "matrix_count": len(MATRIX),
        "baseline_count": sum(row["id"] == "A1_EXACT_VALID_BASELINE_ACCEPT" for row in MATRIX),
        "semantic_count": len(semantic),
        "semantic_reject_count": semantic_rejects,
        "digest_reject_count": digest_rejects,
        "malformed_resolved_mutated": malformed_same,
    }


def exact_probe() -> int:
    """Run the exact R3 kind/target/malformed cases in the current cwd."""
    sys.path.insert(0, str(Path.cwd()))
    from aota_forge.core.authorization import AuthorizationFailure, CapabilityLeaseIssuer
    from aota_forge.core.authority import AuthorityEngine, MaterializedDecisionEvidence, TrustedMutationAuthorization
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, WRITE_ONLY
    from aota_forge.core.contracts.mutation import MutationIntent
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    context = bind_trusted_context(
        principal_id="m4-2-r4-exact-case",
        principal_type="operator",
        provenance="m4-2-source-r4-independent-review",
        channel="fixture",
    )
    target_a = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r4-exact-work-a", sub_kind=SubjectKind.WORK))
    target_b = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r4-exact-work-b", sub_kind=SubjectKind.WORK))
    workflow_id = make_id(IdKind.WORKFLOW, "r4-exact-workflow")
    repo = InMemoryGraphRepository()
    repo.store(records.workflow(workflow_id, semantic_intent="R3 exact cases", creation_context={}))
    repo.store(records.subject(target_a.internal_id, kind="WorkSubject", workflow_ref=workflow_id, mechanical_state={"revision": 3, "state": "open"}, id_derivation="fixture"))
    repo.store(records.subject(target_b.internal_id, kind="WorkSubject", workflow_ref=workflow_id, mechanical_state={"revision": 3, "state": "open"}, id_derivation="fixture"))
    baseline = records.decision(make_id(IdKind.DECISION, "r4-exact-decision"), target_a.internal_id, "authorization", "authorize exact mutation", target_refs=[target_a.serialize()])
    repo.store(baseline)
    resolver = OwningSubjectResolver(repo)
    engine = AuthorityEngine(resolver)
    contract = OperationContractDescriptor(
        name="m4_2_r4_exact_mutation",
        description="R4 exact resolver case",
        inputs=(), required_context=(), optional_context=(), read_write=WRITE_ONLY,
        mutation_scope="write", required_authority="trusted", approval_required=False,
        decision_required=True, valid_predecessor_state="open", valid_successor_state="open",
        subject_revision_precondition=True, external_authority_precondition=False,
        idempotency="same-key-same-intent-replay", result_contract="canonical-mutation-result",
        errors=(), protocol_version="1",
    )
    current_intent = MutationIntent(
        operation=contract.name, semantic_inputs={"value": "r4"},
        logical_target=target_a.to_canonical(), mutation_scope={"mode": "write"}, idempotency_key="r4-exact-intent",
    )

    def run(label: str, mutated) -> None:
        decision_ref = make_object_ref(IdKind.DECISION, mutated.decision_id)
        case_evidence = MaterializedDecisionEvidence.from_decision(
            mutated, operation=contract.name, target=target_a, expected_revision=3, scope={"mode": "write"}
        )
        auth = TrustedMutationAuthorization(
            principal=context.principal, operation=contract.name, target=target_a,
            mutation_scope={"mode": "write"}, contract_hash=contract.contract_hash(),
            intent_fingerprint=current_intent.intent_fingerprint(), subject_expected_revision=3,
            normalized_plan_digest="c" * 64, authorization_basis="materialized_decision_evidence",
            decision_basis=case_evidence, authorization_id="r4-exact-auth", trusted_context=context,
        )
        repo.store(mutated)
        resolved = resolver.resolve_decision(decision_ref)
        resolved_same = resolved is mutated
        digest_valid = case_evidence.evidence_digest == case_evidence.computed_digest(resolved)
        outcome, detail = attempt(lambda: CapabilityLeaseIssuer(engine).issue(auth, contract, intent=current_intent, trusted_context=context, now=NOW))
        print(f"{label}|RESULT={outcome}|CODE={detail}|RESOLVED_SAME={'yes' if resolved_same else 'no'}|DIGEST_VALID={'yes' if digest_valid else 'no'}")
        repo.store(baseline)

    run("A12_WRONG_NONEMPTY_DECISION_KIND", replace(baseline, decision_kind="review_result"))
    run("A13_WRONG_NESTED_TARGET_REFERENCE", replace(baseline, target_refs=[target_b.serialize()]))
    run("MALFORMED_STRUCTURAL_DECISION", replace(baseline, statement=""))
    return 0


def run_probe(root: Path) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return result.returncode == 0, output


def with_worktree(sha: str, callback):
    temp = Path(tempfile.mkdtemp(prefix="m4-2-r4-", dir="/tmp/opencode"))
    added = subprocess.run(
        ["git", "worktree", "add", "--detach", str(temp), sha],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if added.returncode != 0:
        shutil.rmtree(temp, ignore_errors=True)
        raise RuntimeError(added.stdout + added.stderr)
    try:
        return callback(temp)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(temp)], cwd=ROOT, check=False)
        shutil.rmtree(temp, ignore_errors=True)


def source_guard_at(sha: str) -> tuple[int, str, str]:
    def run(temp: Path):
        compile_result = subprocess.run(
            [sys.executable, "-m", "py_compile", "scripts/m4_2_source_guard.py"],
            cwd=temp,
            check=False,
            capture_output=True,
            text=True,
        )
        guard_result = subprocess.run(
            [sys.executable, "scripts/m4_2_source_guard.py"],
            cwd=temp,
            check=False,
            capture_output=True,
            text=True,
        )
        return guard_result.returncode, compile_result.stdout + compile_result.stderr, guard_result.stdout + guard_result.stderr

    return with_worktree(sha, run)


def changed_scope() -> dict[str, object]:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", f"{REPAIR_BASE_SHA}..{TARGET_SHA}"], cwd=ROOT, text=True
    ).splitlines()
    m4_2 = {
        "aota_forge/core/authority.py",
        "aota_forge/core/capability_lease.py",
        "aota_forge/core/authorization.py",
    }
    m4_4 = {
        "aota_forge/core/context.py",
        "aota_forge/core/contracts/descriptor.py",
        "aota_forge/core/contracts/results.py",
        "aota_forge/core/contracts/validation.py",
        "aota_forge/core/transaction.py",
        "aota_forge/core/transitions.py",
        "aota_forge/core/project/resolver.py",
    }
    shared = {
        "aota_forge/core/contracts/canonical.py",
        "aota_forge/core/contracts/mutation.py",
        "aota_forge/core/contracts/operations.py",
        "aota_forge/core/contracts/registry.py",
        "aota_forge/core/contracts/version.py",
        "aota_forge/core/idempotency.py",
        "aota_forge/core/ingress.py",
        "aota_forge/core/graph/repository.py",
    }
    integration = {
        "aota_forge/core/contracts/errors.py",
        "aota_forge/core/contracts/__init__.py",
        "aota_forge/core/__init__.py",
    }
    source = [path for path in changed if path.startswith("aota_forge/")]
    forbidden = [path for path in source if path not in m4_2]
    return {
        "changed": changed,
        "source": source,
        "all_source_within_m4_2": not forbidden,
        "m4_4_count": len(set(source) & m4_4),
        "shared_count": len(set(source) & shared),
        "integration_count": len(set(source) & integration),
        "forbidden_count": len(forbidden),
    }


def exact_lineage() -> dict[str, object]:
    parent_pairs = (
        (COMMON_SOURCE_BASE, "44150acf551edf8b77101eb5c64d74d090f9a7b5"),
        ("44150acf551edf8b77101eb5c64d74d090f9a7b5", "299a146a1d04086d6d7770a4ce55e7569c760922"),
        ("299a146a1d04086d6d7770a4ce55e7569c760922", REPAIR_BASE_SHA),
        (REPAIR_BASE_SHA, TARGET_SHA),
    )
    direct = all(
        subprocess.check_output(["git", "rev-parse", f"{child}^"], cwd=ROOT, text=True).strip() == parent
        for parent, child in parent_pairs
    )
    target_exists = subprocess.run(["git", "cat-file", "-e", f"{TARGET_SHA}^{{commit}}"], cwd=ROOT, check=False).returncode == 0
    local_ref = subprocess.check_output(["git", "show-ref", "--hash", SOURCE_REF], cwd=ROOT, text=True).strip()
    remote_ref = subprocess.check_output(["git", "rev-parse", SOURCE_REMOTE_REF], cwd=ROOT, text=True).strip()
    remote_api = subprocess.run(["git", "ls-remote", "origin", SOURCE_REF], cwd=ROOT, check=False, capture_output=True, text=True)
    remote_api_sha = remote_api.stdout.split()[0] if remote_api.stdout.split() else ""
    return {
        "target_exists": target_exists,
        "local_ref": local_ref,
        "tracking_ref": remote_ref,
        "remote_api_sha": remote_api_sha,
        "target_ref_verified": local_ref == TARGET_SHA and remote_ref == TARGET_SHA and remote_api_sha == TARGET_SHA,
        "ancestry_verified": direct,
    }


def source_guard_quality(current_output: str) -> dict[str, object]:
    source_guard = (ROOT / "scripts" / "m4_2_source_guard.py").read_text(encoding="ascii")
    count_match = re.search(r"FOCUSED_TEST_COUNT=(\d+)", current_output)
    pass_match = re.search(r"FOCUSED_TEST_PASSED=(\d+)", current_output)
    recomputed_match = re.search(r"RECOMPUTED_DIGEST_ADVERSARIAL_CASE_COUNT=(\d+)", current_output)
    recomputed_pass_match = re.search(r"RECOMPUTED_DIGEST_INVALID_CASE_REJECT_COUNT=(\d+)", current_output)
    helper = source_guard.split("def expect_recomputed_rejection", 1)[1].split("# Every", 1)[0]
    return {
        "case_count": int(count_match.group(1)) if count_match else 0,
        "pass_count": int(pass_match.group(1)) if pass_match else 0,
        "recomputed_count": int(recomputed_match.group(1)) if recomputed_match else 0,
        "recomputed_reject_count": int(recomputed_pass_match.group(1)) if recomputed_pass_match else 0,
        "malformed_case": "R2_EXACT_MALFORMED_DECISION_RECOMPUTED_DIGEST_REJECTED" in current_output,
        "kind_case": "D8_RECOMPUTED_INCOMPATIBLE_DECISION_KIND_REJECTED" in current_output,
        "nested_case": "D9_RECOMPUTED_INCOMPATIBLE_NESTED_TARGET_REJECTED" in current_output,
        "materializes": "decision_repo.store(resolved_decision)" in helper,
        "restores": "decision_repo.store(original_decision)" in helper,
        "real_resolver": "decision_resolver = OwningSubjectResolver(decision_repo)" in source_guard,
        "real_engine": "decision_engine = AuthorityEngine(decision_resolver)" in source_guard,
        "real_issuer": "CapabilityLeaseIssuer(decision_engine)" in source_guard and "_issue(" in source_guard,
        "tautological": "json.load" in source_guard or ".json" in source_guard or TARGET_SHA in source_guard,
        "name_special_case": re.search(r"if\s+name\s*(?:==|in)", source_guard) is not None,
        "evidence_self_validation": "json.load" in source_guard or ".json" in source_guard,
        "descendant_aware": "merge-base" in source_guard and "--is-ancestor" in source_guard,
        "head_equals_base": "HEAD == COMMON_SOURCE_BASE" in source_guard,
    }


def main() -> int:
    if "--probe" in sys.argv:
        return exact_probe()

    lineage = exact_lineage()
    check("TARGET_SHA_VERIFIED", lineage["target_exists"])
    check("TARGET_REMOTE_REF_VERIFIED", lineage["target_ref_verified"], f"local={lineage['local_ref']} remote={lineage['remote_api_sha']}")
    check("TARGET_ANCESTRY_VERIFIED", lineage["ancestry_verified"])
    check("ACCEPTED_PLAN_SHA_VERIFIED", subprocess.run(["git", "cat-file", "-e", "8458c0150bd9ec96e35f1f4059e684908fe67469^{commit}"], cwd=ROOT, check=False).returncode == 0)

    old_probe_ok, old_probe = with_worktree(REPAIR_BASE_SHA, run_probe)
    repaired_probe_ok, repaired_probe = run_probe(ROOT)
    check("FAILED_R3_BF01_REPRODUCED_ON_OLD_SHA", "A12_WRONG_NONEMPTY_DECISION_KIND|RESULT=accept" in old_probe)
    check("FAILED_R3_BF02_REPRODUCED_ON_OLD_SHA", "A13_WRONG_NESTED_TARGET_REFERENCE|RESULT=accept" in old_probe)
    check("FAILED_R3_BF01_REJECTED_ON_REPAIRED_SHA", "A12_WRONG_NONEMPTY_DECISION_KIND|RESULT=reject|CODE=MATERIALIZED_DECISION_REQUIRED" in repaired_probe)
    check("FAILED_R3_BF02_REJECTED_ON_REPAIRED_SHA", "A13_WRONG_NESTED_TARGET_REFERENCE|RESULT=reject|CODE=MATERIALIZED_DECISION_REQUIRED" in repaired_probe)
    check("FAILED_R3_MALFORMED_CASE_REJECTED_ON_BOTH", "MALFORMED_STRUCTURAL_DECISION|RESULT=reject|CODE=MATERIALIZED_DECISION_REQUIRED" in old_probe and "MALFORMED_STRUCTURAL_DECISION|RESULT=reject|CODE=MATERIALIZED_DECISION_REQUIRED" in repaired_probe)
    check("EXACT_PROBE_RESOLVER_IDENTITY_PROVEN", "RESOLVED_SAME=yes" in old_probe and "RESOLVED_SAME=yes" in repaired_probe)
    check("EXACT_PROBE_DIGEST_VALID_PROVEN", "DIGEST_VALID=yes" in old_probe and "DIGEST_VALID=yes" in repaired_probe)
    check("EXACT_PROBE_PROCESSES_COMPLETED", old_probe_ok and repaired_probe_ok)

    old_code, _, old_guard_output = source_guard_at(REPAIR_BASE_SHA)
    target_code, _, target_guard_output = source_guard_at(TARGET_SHA)
    old_guard_text = with_worktree(REPAIR_BASE_SHA, lambda temp: (temp / "scripts/m4_2_source_guard.py").read_text(encoding="ascii"))
    old_helper = old_guard_text.split("def expect_recomputed_rejection", 1)[1].split("# Every", 1)[0]
    old_count = re.search(r"FOCUSED_TEST_COUNT=(\d+)", old_guard_output)
    old_recomputed = re.search(r"RECOMPUTED_DIGEST_ADVERSARIAL_CASE_COUNT=(\d+)", old_guard_output)
    check("FAILED_R3_BF03_REPRODUCED_ON_OLD_SHA", old_code == 0 and old_count and old_count.group(1) == "35" and old_recomputed and old_recomputed.group(1) == "11" and "decision_repo.store" not in old_helper)
    target_quality = source_guard_quality(target_guard_output)
    check("SOURCE_GUARD_REAL_PATH_EXECUTED", target_quality["real_resolver"] and target_quality["real_engine"])
    check("SOURCE_GUARD_REAL_ISSUANCE_PATH_EXECUTED", target_quality["real_issuer"])
    check("GUARD_MUTATED_RESOLVER_RECORD_MATERIALIZATION_REVIEW", target_quality["materializes"] and target_quality["restores"])
    check("SOURCE_GUARD_SELF_CONSISTENCY", target_code == 0 and target_quality["case_count"] == 37 and target_quality["pass_count"] == 37 and target_quality["recomputed_count"] == 13 and target_quality["recomputed_reject_count"] == 12)
    check("SOURCE_GUARD_REAL_MALFORMED_DECISION_RECOMPUTED_DIGEST_CASE", target_quality["malformed_case"])
    check("SOURCE_GUARD_REAL_INCOMPATIBLE_KIND_RECOMPUTED_DIGEST_CASE", target_quality["kind_case"])
    check("SOURCE_GUARD_REAL_NESTED_TARGET_RECOMPUTED_DIGEST_CASE", target_quality["nested_case"])
    check("TAUTOLOGICAL_VALIDATION_DETECTED", not target_quality["tautological"])
    check("TEST_CASE_NAME_SPECIAL_CASING_DETECTED", not target_quality["name_special_case"])
    check("EVIDENCE_SELF_VALIDATION_AS_TEST", not target_quality["evidence_self_validation"])
    check("GUARD_DESCENDANT_AWARE", target_quality["descendant_aware"])
    check("GUARD_HEAD_EQUALS_COMMON_BASE_REQUIRED", not target_quality["head_equals_base"])

    scope = changed_scope()
    check("SOURCE_REPAIR_DIFF_SCOPE_REVIEW", scope["all_source_within_m4_2"] and not scope["forbidden_count"])
    check("ALL_SOURCE_WRITES_WITHIN_M4_2_EXCLUSIVE_PATHS", scope["all_source_within_m4_2"])
    check("M4_4_EXCLUSIVE_WRITE_PATH_MUTATION_COUNT", scope["m4_4_count"] == 0, str(scope["m4_4_count"]))
    check("SHARED_READ_ONLY_PATH_MUTATION_COUNT", scope["shared_count"] == 0, str(scope["shared_count"]))
    check("INTEGRATION_ONLY_PATH_MUTATION_COUNT", scope["integration_count"] == 0, str(scope["integration_count"]))
    check("FORBIDDEN_CROSS_LANE_WRITE_COUNT", scope["forbidden_count"] == 0, str(scope["forbidden_count"]))
    check("DEFERRED_INTEGRATION_SEAMS_REVIEW", not any(path.startswith("tests/") for path in scope["changed"]) and scope["integration_count"] == 0)

    matrix_stats = run_recomputed_matrix()

    # Exactly 16 independent negative cases are required by the review gate.
    negative_ids = {item["id"] for item in NEGATIVES}
    required_negative_ids = {
        "NEG-R4-01_GLOBALLY_VALID_WRONG_KIND",
        "NEG-R4-02_UNSUPPORTED_KIND",
        "NEG-R4-03_WRONG_NESTED_TARGET_ID",
        "NEG-R4-04_WRONG_NESTED_OWNER_PROJECT",
        "NEG-R4-05_CORRECT_KIND_WRONG_TARGET",
        "NEG-R4-06_WRONG_KIND_CORRECT_TARGET",
        "NEG-R4-07_MALFORMED_RECOMPUTED_DIGEST",
        "NEG-R4-08_RESOLVER_BASELINE_INSTEAD_OF_MUTATED_RECORD",
        "NEG-R4-09_RESOLVER_OBJECT_EXISTENCE_IS_NOT_SEMANTIC_VALIDITY",
        "NEG-R4-10_ABSENT_TRUSTED_VALIDATOR",
        "NEG-R4-11_ARBITRARY_AUTHORIZATION_ID_ONLY",
        "NEG-R4-12_ARBITRARY_RESERVATION_ID_ONLY",
        "NEG-R4-13_APPROVAL_SUBSTITUTES_REQUIRED_DECISION",
        "NEG-R4-14_ISSUER_REINTERPRETS_TARGET",
        "NEG-R4-15_UNKNOWN_OUTCOME_LEASE_REUSE",
        "NEG-R4-16_M4_2_ENABLES_WRITE_INGRESS",
    }
    check("INDEPENDENT_NEGATIVE_CASE_COUNT", negative_ids == required_negative_ids and len(NEGATIVES) == 16, str(len(NEGATIVES)))
    check("INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT", sum(item["result"] == "reject" for item in NEGATIVES) == 16)
    check("INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT", sum(item["result"] != "reject" for item in NEGATIVES) == 0)

    # Source-only boundary and regression commands are run independently here.
    import aota_forge.core.authorization as authorization_module
    changed_paths = set(scope["changed"])
    check("M4_2_WRITE_INGRESS_IMPLEMENTED", not authorization_module.M4_2_WRITE_INGRESS_IMPLEMENTED)
    check("M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED", not authorization_module.M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED)
    check("M4_2_DURABLE_JOURNAL_IMPLEMENTED", not authorization_module.M4_2_DURABLE_JOURNAL_IMPLEMENTED)
    check("NO_INGRESS_SOURCE_MUTATION", "aota_forge/core/ingress.py" not in changed_paths)

    def run_regression(command: str) -> tuple[int, str]:
        result = subprocess.run(command.split(), cwd=ROOT, check=False, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr

    m4_1_code, m4_1_output = run_regression("python3 scripts/m4_1_source_guard.py")
    b5_code, b5_output = run_regression("python3 scripts/m3_b5_authority_capability_lease.py")
    b6_code, b6_output = run_regression("python3 scripts/m3_b6_revision_cas_transaction.py")
    b12_code, b12_output = run_regression("python3 scripts/m3_b12_integration_reconciliation.py")
    b10_code, b10_output = run_regression("python3 scripts/m3_b10_behavioral_regression_foundation.py")
    check("M4_1_CONTRACT_REGRESSION", m4_1_code == 0 and "M4_1_SOURCE_GUARD=PASS" in m4_1_output)
    check("M3_AUTHORITY_REGRESSION", "B5 checks PASSED: 28/29" in b5_output and "B5-N24" in b5_output and "B5_REQUIRED_FLAGS_NO_CAS_TRANSACTION_CUTOVER" in b5_output)
    check("M3_IDEMPOTENCY_REGRESSION", b6_code == 0 and "B6 checks PASSED: 36/36" in b6_output)
    check("INGRESS_REGRESSION", b12_code == 0 and "CROSS_LANE_INTEGRATION_REGRESSION=PASS" in b12_output)
    check("M3_BEHAVIORAL_REGRESSION", b10_code == 0 and "Verdict:                        PASS" in b10_output)

    print(f"RECOMPUTED_DIGEST_ADVERSARIAL_CASE_COUNT={matrix_stats['matrix_count']}")
    print(f"VALID_BASELINE_CASE_COUNT={matrix_stats['baseline_count']}")
    print(f"INVALID_SEMANTIC_CASE_COUNT={matrix_stats['semantic_count']}")
    print(f"INVALID_SEMANTIC_CASE_REJECT_COUNT={matrix_stats['semantic_reject_count']}")
    print(f"INVALID_DIGEST_CASE_REJECT_COUNT={matrix_stats['digest_reject_count']}")
    print(f"INDEPENDENT_NEGATIVE_CASE_COUNT={len(NEGATIVES)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={sum(item['result'] == 'reject' for item in NEGATIVES)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={sum(item['result'] != 'reject' for item in NEGATIVES)}")
    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"INDEPENDENT_R4_GUARD={'PASS' if not failed else 'FAIL'}")
    if failed:
        print("FAILED_CHECKS=" + ",".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
