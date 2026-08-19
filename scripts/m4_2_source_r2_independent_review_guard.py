#!/usr/bin/env python3
"""Independent review-only probes for the repaired M4-2 source candidate."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE_SHA = "d74953be16b103fbd09b0ee18b203881244c4f95"
TARGET_SHA = "299a146a1d04086d6d7770a4ce55e7569c760922"
TARGET_REF = "refs/remotes/origin/aota/m4/m4-2-source"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[tuple[str, bool, str]] = []
NEGATIVE_RESULTS: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f" ({detail})" if detail else ""))
    return passed


def expect_code(call, code: str) -> bool:
    from aota_forge.core.authorization import AuthorizationFailure

    try:
        call()
    except AuthorizationFailure as exc:
        return exc.code == code
    except Exception:
        return False
    return False


def target_fixture():
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    context = bind_trusted_context(
        principal_id="m4-2-r2-reviewer",
        principal_type="operator",
        provenance="m4-2-r2-independent-review",
        channel="fixture",
    )
    refs = {
        "work_a": make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "r2-work-a", sub_kind=SubjectKind.WORK),
        ),
        "work_b": make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "r2-work-b", sub_kind=SubjectKind.WORK),
        ),
        "project_a": make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "r2-project-a", sub_kind=SubjectKind.PROJECT),
        ),
        "project_b": make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "r2-project-b", sub_kind=SubjectKind.PROJECT),
        ),
        "milestone_a": make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "r2-milestone-a", sub_kind=SubjectKind.PLAN),
        ),
        "milestone_b": make_object_ref(
            IdKind.SUBJECT,
            make_id(IdKind.SUBJECT, "r2-milestone-b", sub_kind=SubjectKind.PLAN),
        ),
    }
    return context, refs


def descriptor(*, decision_required: bool = False, external: bool = False):
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, WRITE_ONLY

    return OperationContractDescriptor(
        name="m4_2_r2_mutation",
        description="independent M4-2 R2 fixture",
        inputs=(),
        required_context=(),
        optional_context=(),
        read_write=WRITE_ONLY,
        mutation_scope="write",
        required_authority="trusted",
        approval_required=False,
        decision_required=decision_required,
        valid_predecessor_state="open",
        valid_successor_state="open",
        subject_revision_precondition=True,
        external_authority_precondition=external,
        idempotency="same-key-same-intent-replay",
        result_contract="canonical-mutation-result",
        errors=(),
        protocol_version="1",
    )


def intent(target):
    from aota_forge.core.contracts.mutation import MutationIntent

    return MutationIntent(
        operation="m4_2_r2_mutation",
        semantic_inputs={"value": "independent"},
        logical_target=target.to_canonical(),
        mutation_scope={"mode": "write"},
        idempotency_key="r2-intent-key",
    )


def authorization(
    context,
    target,
    contract,
    *,
    basis="trusted_scope_no_extra_approval",
    approval=None,
    decision=None,
    authorization_id="r2-authorization",
    reservation_ref=None,
    external=False,
):
    from aota_forge.core.authority import TrustedMutationAuthorization

    return TrustedMutationAuthorization(
        principal=context.principal,
        operation=contract.name,
        target=target,
        mutation_scope={"mode": "write"},
        contract_hash=contract.contract_hash(),
        intent_fingerprint=intent(target).intent_fingerprint(),
        subject_expected_revision=3,
        external_authority_precondition="raw-revision-3" if external else None,
        authority_source_revision="3" if external else None,
        authority_observed_raw_digest="a" * 64 if external else None,
        candidate_raw_digest="b" * 64 if external else None,
        normalized_plan_digest="c" * 64,
        authorization_basis=basis,
        approval_basis=approval,
        decision_basis=decision,
        authorization_id=authorization_id,
        reservation_ref=reservation_ref,
        trusted_context=context,
    )


def graph_fixture(refs):
    from aota_forge.core.authority import AuthorityEngine, MaterializedDecisionEvidence
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind

    workflow_id = make_id(IdKind.WORKFLOW, "r2-decision-workflow")
    repo = InMemoryGraphRepository()
    repo.store(records.workflow(workflow_id, semantic_intent="R2 decision fixture", creation_context={}))
    for ref in refs.values():
        repo.store(
            records.subject(
                ref.internal_id,
                kind="Subject",
                workflow_ref=workflow_id,
                mechanical_state={"revision": 3, "state": "open"},
                id_derivation="fixture",
            )
        )

    decisions = {}
    for name, ref in refs.items():
        decision = records.decision(
            make_id(IdKind.DECISION, f"r2-decision-{name}"),
            ref.internal_id,
            "authorization",
            f"authorize {name}",
        )
        repo.store(decision)
        decisions[name] = decision

    engine = AuthorityEngine(OwningSubjectResolver(repo))

    def evidence(decision, target, operation, scope=None):
        return MaterializedDecisionEvidence.from_decision(
            decision,
            operation=operation,
            target=target,
            expected_revision=3,
            scope=scope or {"mode": "write"},
        )

    return repo, engine, decisions, evidence


def issue(issuer, auth, contract, context, target, *, now=NOW, **kwargs):
    return issuer.issue(
        auth,
        contract,
        intent=intent(target),
        trusted_context=context,
        now=now,
        **kwargs,
    )


def run_clean_source_guard() -> subprocess.CompletedProcess[str]:
    temp = tempfile.mkdtemp(prefix="m4-2-r2-clean-", dir="/tmp/opencode")
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", temp, TARGET_SHA],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return subprocess.run(
            [sys.executable, str(Path(temp) / "scripts" / "m4_2_source_guard.py")],
            cwd=temp,
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", temp], cwd=ROOT, check=False)
        shutil.rmtree(temp, ignore_errors=True)


def ownership_invalid_descendant_rejected() -> bool:
    temp = tempfile.mkdtemp(prefix="m4-2-r2-invalid-", dir="/tmp/opencode")
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", temp, TARGET_SHA],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        forbidden = Path(temp) / "aota_forge" / "core" / "r2_forbidden_probe.py"
        forbidden.parent.mkdir(parents=True, exist_ok=True)
        forbidden.write_text("FORBIDDEN_R2_PROBE = True\n", encoding="ascii")
        subprocess.run(["git", "add", str(forbidden)], cwd=temp, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=M4-2 R2 review fixture",
                "-c",
                "user.email=m4-2-r2-review@example.invalid",
                "commit",
                "-m",
                "test(m4-2): add forbidden review fixture path",
            ],
            cwd=temp,
            check=True,
            capture_output=True,
            text=True,
        )
        result = subprocess.run(
            [sys.executable, str(Path(temp) / "scripts" / "m4_2_source_guard.py")],
            cwd=temp,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode != 0 and "SOURCE_PARTITION_EXACT" in result.stdout
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", temp], cwd=ROOT, check=False)
        shutil.rmtree(temp, ignore_errors=True)


def run_required_negative_cases(context, refs, engine, decisions, evidence):
    from aota_forge.core.authorization import AuthorizationErrorCode, CapabilityLeaseIssuer
    from aota_forge.core.authority import ApprovalEvidence, MaterializedDecisionEvidence

    standard = descriptor(external=True)
    standard_auth = authorization(context, refs["work_a"], standard, external=True)
    issuer = CapabilityLeaseIssuer()
    engine_issuer = CapabilityLeaseIssuer(engine)

    lease = issue(issuer, standard_auth, standard, context, refs["work_a"], lease_id="r2-lease", attempt_id="r2-attempt")

    def negative(case_id, call, code):
        passed = expect_code(call, code)
        NEGATIVE_RESULTS.append((case_id, passed))
        check(case_id, passed)

    decision_contract = descriptor(decision_required=True)
    unresolved = replace(
        evidence(decisions["work_a"], refs["work_a"], decision_contract.name),
        decision_ref=replace(
            evidence(decisions["work_a"], refs["work_a"], decision_contract.name).decision_ref,
            internal_id=type(decisions["work_a"].decision_id)(
                kind=decisions["work_a"].decision_id.kind,
                value="unresolved-r2-decision",
                sub_kind=decisions["work_a"].decision_id.sub_kind,
            ),
        ),
    )
    valid_auth = authorization(
        context,
        refs["work_a"],
        decision_contract,
        basis="materialized_decision_evidence",
        decision=evidence(decisions["work_a"], refs["work_a"], decision_contract.name),
    )

    negative(
        "NEG-R2-03",
        lambda: issue(
            engine_issuer,
            replace(valid_auth, decision_basis=unresolved),
            decision_contract,
            context,
            refs["work_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    negative(
        "NEG-R2-04",
        lambda: issue(CapabilityLeaseIssuer(), valid_auth, decision_contract, context, refs["work_a"]),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    negative(
        "NEG-R2-05",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["work_a"],
                decision_contract,
                basis="materialized_decision_evidence",
                decision=evidence(decisions["work_b"], refs["work_a"], decision_contract.name),
            ),
            decision_contract,
            context,
            refs["work_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    negative(
        "NEG-R2-06",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["work_a"],
                decision_contract,
                basis="materialized_decision_evidence",
                decision=evidence(decisions["work_a"], refs["work_a"], "other-operation"),
            ),
            decision_contract,
            context,
            refs["work_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    negative(
        "NEG-R2-07",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["project_a"],
                descriptor(),
                basis="project_milestone_semantic_decision",
                authorization_id="arbitrary-only-auth-id",
            ),
            descriptor(),
            context,
            refs["project_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    negative(
        "NEG-R2-08",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["project_a"],
                descriptor(),
                basis="project_milestone_semantic_decision",
                authorization_id=None,
                reservation_ref="arbitrary-only-reservation",
            ),
            descriptor(),
            context,
            refs["project_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    project_contract = descriptor()
    negative(
        "NEG-R2-09",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["project_a"],
                project_contract,
                basis="project_milestone_semantic_decision",
                decision=evidence(decisions["project_b"], refs["project_b"], project_contract.name),
            ),
            project_contract,
            context,
            refs["project_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    milestone_contract = descriptor()
    negative(
        "NEG-R2-10",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["milestone_a"],
                milestone_contract,
                basis="project_milestone_semantic_decision",
                decision=evidence(decisions["milestone_b"], refs["milestone_b"], milestone_contract.name),
            ),
            milestone_contract,
            context,
            refs["milestone_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    approval_contract = descriptor(decision_required=True)
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
    negative(
        "NEG-R2-11",
        lambda: issue(
            engine_issuer,
            authorization(
                context,
                refs["work_a"],
                approval_contract,
                basis="approval_evidence",
                approval=approval,
                decision=None,
            ),
            approval_contract,
            context,
            refs["work_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    negative(
        "NEG-R2-12",
        lambda: issuer.issue(standard_auth, standard, trusted_context=context, now=NOW, target=[refs["work_a"], refs["work_b"]]),
        AuthorizationErrorCode.NEEDS_SEMANTIC_CHOICE.value,
    )
    negative(
        "NEG-R2-13",
        lambda: issuer.validate_lease(
            lease.mark_outcome_unknown(),
            trusted_context=context,
            operation=standard.name,
            target=refs["work_a"],
            mutation_scope={"mode": "write"},
            contract_hash=standard.contract_hash(),
            intent_fingerprint=intent(refs["work_a"]).intent_fingerprint(),
            subject_expected_revision=3,
            external_authority_precondition="raw-revision-3",
            authority_source_revision="3",
            authority_observed_raw_digest="a" * 64,
            candidate_raw_digest="b" * 64,
            normalized_plan_digest="c" * 64,
            now=NOW,
        ),
        AuthorizationErrorCode.OUTCOME_UNKNOWN_REQUIRES_RECONCILIATION.value,
    )
    return lease


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from aota_forge.core.authorization import AuthorizationErrorCode, CapabilityLeaseIssuer
    from aota_forge.core.authority import AuthorityEngine, MaterializedDecisionEvidence
    from aota_forge.core.capability_lease import LEASE_OUTCOME_UNKNOWN

    target_commit = subprocess.run(
        ["git", "cat-file", "-e", f"{TARGET_SHA}^{{commit}}"], cwd=ROOT, check=False
    ).returncode == 0
    target_ref = subprocess.check_output(["git", "rev-parse", TARGET_REF], cwd=ROOT, text=True).strip()
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", BASE_SHA, TARGET_SHA], cwd=ROOT, check=False
    ).returncode == 0
    check("TARGET_SHA_VERIFIED", target_commit and TARGET_SHA == target_ref)
    check("TARGET_ANCESTRY_VERIFIED", ancestry)

    guard = run_clean_source_guard()
    guard_output = guard.stdout
    check(
        "NEG-R2-01",
        guard.returncode == 0
        and f"head={TARGET_SHA}" in guard_output
        and "FOCUSED_TEST_COUNT=23" in guard_output
        and "FOCUSED_TEST_PASSED=23" in guard_output,
        "descendant candidate is accepted without exact-base equality",
    )
    NEGATIVE_RESULTS.append(("NEG-R2-01", RESULTS[-1][1]))
    ownership_ok = ownership_invalid_descendant_rejected()
    check("NEG-R2-02", ownership_ok, "ownership-invalid descendant is rejected")
    NEGATIVE_RESULTS.append(("NEG-R2-02", ownership_ok))

    guard_source = (ROOT / "scripts" / "m4_2_source_guard.py").read_text(encoding="ascii")
    check(
        "SOURCE_GUARD_SELF_CONSISTENCY",
        guard.returncode == 0 and "FOCUSED_TEST_COUNT=23" in guard_output and "FOCUSED_TEST_PASSED=23" in guard_output,
    )
    check(
        "TAUTOLOGICAL_VALIDATION_DETECTED",
        not any(token in guard_source for token in ("json.load", ".json", "if name ==", "if name in", TARGET_SHA)),
        "no evidence self-validation or target/name special casing",
    )
    check(
        "TEST_CASE_NAME_SPECIAL_CASING_DETECTED",
        not re.search(r"if\s+name\s*(?:==|in)", guard_source),
    )
    check("EVIDENCE_SELF_VALIDATION_AS_TEST", "json.load" not in guard_source and ".json" not in guard_source)

    context, refs = target_fixture()
    repo, engine, decisions, evidence = graph_fixture(refs)
    decision_contract = descriptor(decision_required=True)
    valid_evidence = evidence(decisions["work_a"], refs["work_a"], decision_contract.name)
    valid_auth = authorization(
        context,
        refs["work_a"],
        decision_contract,
        basis="materialized_decision_evidence",
        decision=valid_evidence,
    )
    try:
        valid_lease = issue(CapabilityLeaseIssuer(engine), valid_auth, decision_contract, context, refs["work_a"])
    except Exception as exc:
        check("D1_VALID_EXACT_RESOLVED_DECISION", False, type(exc).__name__)
    else:
        check("D1_VALID_EXACT_RESOLVED_DECISION", valid_lease is not None)

    invalid_digest = replace(valid_evidence, evidence_digest="0" * 64)
    check(
        "D3_INVALID_DECISION_EVIDENCE_REJECTED",
        expect_code(
            lambda: issue(
                CapabilityLeaseIssuer(engine),
                replace(valid_auth, decision_basis=invalid_digest),
                decision_contract,
                context,
                refs["work_a"],
            ),
            AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
        ),
    )
    malformed = __import__("aota_forge.core.graph.records", fromlist=["decision"]).decision(
        decisions["work_a"].decision_id,
        refs["work_a"].internal_id,
        "",
        "",
    )
    repo.store(malformed)
    malformed_evidence = MaterializedDecisionEvidence.from_decision(
        malformed,
        operation=decision_contract.name,
        target=refs["work_a"],
        expected_revision=3,
        scope={"mode": "write"},
    )
    malformed_result = expect_code(
        lambda: issue(
            CapabilityLeaseIssuer(AuthorityEngine(__import__("aota_forge.core.graph.repository", fromlist=["OwningSubjectResolver"]).OwningSubjectResolver(repo))),
            replace(valid_auth, decision_basis=malformed_evidence),
            decision_contract,
            context,
            refs["work_a"],
        ),
        AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
    )
    check("D3_INVALID_RESOLVED_DECISION_RECORD_REJECTED", malformed_result, "malformed Decision fields must not become semantic authority")

    unresolved = replace(
        valid_evidence,
        decision_ref=replace(valid_evidence.decision_ref, internal_id=replace(valid_evidence.decision_ref.internal_id, value="r2-unresolved")),
    )
    check(
        "D2_UNRESOLVED_DECISION_REJECTED",
        expect_code(
            lambda: issue(CapabilityLeaseIssuer(engine), replace(valid_auth, decision_basis=unresolved), decision_contract, context, refs["work_a"]),
            AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
        ),
    )
    check(
        "D8_MISSING_AUTHORITY_ENGINE_FAILS_CLOSED",
        expect_code(
            lambda: issue(CapabilityLeaseIssuer(), valid_auth, decision_contract, context, refs["work_a"]),
            AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value,
        ),
    )

    standard_lease = run_required_negative_cases(context, refs, engine, decisions, evidence)
    check(
        "NEG-R2-14",
        not __import__("aota_forge.core.authorization", fromlist=["M4_2_WRITE_INGRESS_IMPLEMENTED"]).M4_2_WRITE_INGRESS_IMPLEMENTED
        and not __import__("aota_forge.core.authorization", fromlist=["M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED"]).M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED
        and not __import__("aota_forge.core.authorization", fromlist=["M4_2_DURABLE_JOURNAL_IMPLEMENTED"]).M4_2_DURABLE_JOURNAL_IMPLEMENTED
        and "aota_forge/core/ingress.py" not in subprocess.check_output(["git", "diff", "--name-only", BASE_SHA, TARGET_SHA], cwd=ROOT, text=True),
        "M4-2 source does not enable write ingress",
    )
    NEGATIVE_RESULTS.append(("NEG-R2-14", RESULTS[-1][1]))

    check("UNKNOWN_OUTCOME_STATE_EXPLICIT", standard_lease.mark_outcome_unknown().outcome_state == LEASE_OUTCOME_UNKNOWN)
    check("SOURCE_GUARD", guard.returncode == 0)
    print(f"SOURCE_GUARD_REAL_CASE_COUNT=23")
    print(f"SOURCE_GUARD_REAL_CASE_PASS_COUNT={23 if guard.returncode == 0 else 0}")
    print(f"INDEPENDENT_NEGATIVE_CASE_COUNT={len(NEGATIVE_RESULTS)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={sum(ok for _, ok in NEGATIVE_RESULTS)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={len(NEGATIVE_RESULTS) - sum(ok for _, ok in NEGATIVE_RESULTS)}")
    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"INDEPENDENT_REVIEW_GUARD={'PASS' if not failed else 'FAIL'}")
    if failed:
        print("FAILED_CHECKS=" + ",".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
