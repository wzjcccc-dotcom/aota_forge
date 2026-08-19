#!/usr/bin/env python3
"""Independent review probes for M4-2 resolver-backed Decision evidence."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COMMON_SOURCE_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
OLD_TARGET_SHA = "299a146a1d04086d6d7770a4ce55e7569c760922"
TARGET_SHA = "cf456734b0490c8be1d0341bc58f0915b18f9619"
SOURCE_REF = "refs/heads/aota/m4/m4-2-source"
NOW = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
RESULTS: list[tuple[str, bool, str]] = []
NEGATIVE_RESULTS: list[tuple[str, bool]] = []
RECOMPUTED_RESULTS: list[tuple[str, str, str]] = []


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


def expect_rejection(call, code: str) -> bool:
    result, detail = attempt(call)
    return result == "reject" and detail == code


def negative(name: str, call, code: str) -> bool:
    passed = expect_rejection(call, code)
    NEGATIVE_RESULTS.append((name, passed))
    check(name, passed)
    return passed


def exact_probe_source() -> str:
    return r'''from pathlib import Path
from datetime import datetime, timezone
import sys
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
    principal_id="m4-2-r3-exact-case",
    principal_type="operator",
    provenance="m4-2-r3-independent-review",
    channel="fixture",
)
target = make_object_ref(
    IdKind.SUBJECT,
    make_id(IdKind.SUBJECT, "r3-exact-work", sub_kind=SubjectKind.WORK),
)
workflow_id = make_id(IdKind.WORKFLOW, "r3-exact-workflow")
repo = InMemoryGraphRepository()
repo.store(records.workflow(workflow_id, semantic_intent="R2 exact malformed Decision case", creation_context={}))
repo.store(records.subject(
    target.internal_id,
    kind="WorkSubject",
    workflow_ref=workflow_id,
    mechanical_state={"revision": 3, "state": "open"},
    id_derivation="fixture",
))
decision_id = make_id(IdKind.DECISION, "r3-exact-decision")
valid = records.decision(decision_id, target.internal_id, "authorization", "authorize exact mutation")
repo.store(valid)
malformed = records.decision(decision_id, target.internal_id, "", "")
repo.store(malformed)
descriptor = OperationContractDescriptor(
    name="m4_2_r3_exact_mutation",
    description="exact R2 malformed Decision case",
    inputs=(),
    required_context=(),
    optional_context=(),
    read_write=WRITE_ONLY,
    mutation_scope="write",
    required_authority="trusted",
    approval_required=False,
    decision_required=True,
    valid_predecessor_state="open",
    valid_successor_state="open",
    subject_revision_precondition=True,
    external_authority_precondition=False,
    idempotency="same-key-same-intent-replay",
    result_contract="canonical-mutation-result",
    errors=(),
    protocol_version="1",
)
intent = MutationIntent(
    operation=descriptor.name,
    semantic_inputs={"value": "exact"},
    logical_target=target.to_canonical(),
    mutation_scope={"mode": "write"},
    idempotency_key="r3-exact-intent",
)
evidence = MaterializedDecisionEvidence.from_decision(
    malformed,
    operation=descriptor.name,
    target=target,
    expected_revision=3,
    scope={"mode": "write"},
)
authorization = TrustedMutationAuthorization(
    principal=context.principal,
    operation=descriptor.name,
    target=target,
    mutation_scope={"mode": "write"},
    contract_hash=descriptor.contract_hash(),
    intent_fingerprint=intent.intent_fingerprint(),
    subject_expected_revision=3,
    normalized_plan_digest="c" * 64,
    authorization_basis="materialized_decision_evidence",
    decision_basis=evidence,
    authorization_id="r3-exact-authorization",
    trusted_context=context,
)
try:
    CapabilityLeaseIssuer(AuthorityEngine(OwningSubjectResolver(repo))).issue(
        authorization,
        descriptor,
        intent=intent,
        trusted_context=context,
        now=datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )
except AuthorizationFailure as exc:
    print(f"REJECTED:{exc.code}")
except Exception as exc:
    print(f"UNEXPECTED:{type(exc).__name__}:{exc}")
else:
    print("ACCEPTED")
'''


def run_exact_probe(cwd: Path, probe: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(probe)],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def verify_old_and_repaired_exact_case() -> tuple[bool, bool, str, str]:
    probe_fd, probe_path = tempfile.mkstemp(prefix="m4-2-r3-exact-", suffix=".py", dir="/tmp/opencode")
    os.close(probe_fd)
    probe = Path(probe_path)
    try:
        probe.write_text(exact_probe_source(), encoding="ascii")
        repaired = run_exact_probe(ROOT, probe)
        temp = Path(tempfile.mkdtemp(prefix="m4-2-r3-old-", dir="/tmp/opencode"))
        try:
            added = subprocess.run(
                ["git", "worktree", "add", "--detach", str(temp), OLD_TARGET_SHA],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            if added.returncode != 0:
                return False, False, "worktree add failed", repaired.stdout + repaired.stderr
            old = run_exact_probe(temp, probe)
            old_output = (old.stdout + old.stderr).strip()
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(temp)], cwd=ROOT, check=False)
            shutil.rmtree(temp, ignore_errors=True)
        repaired_output = (repaired.stdout + repaired.stderr).strip()
        old_ok = old_output == "ACCEPTED"
        repaired_ok = repaired_output == "REJECTED:MATERIALIZED_DECISION_REQUIRED"
        return old_ok, repaired_ok, old_output, repaired_output
    finally:
        probe.unlink(missing_ok=True)


def fixture():
    from aota_forge.core.context import bind_trusted_context
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref

    context = bind_trusted_context(
        principal_id="m4-2-r3-reviewer",
        principal_type="operator",
        provenance="m4-2-source-r3-independent-review",
        channel="fixture",
    )
    refs = {
        "work_a": make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r3-work-a", sub_kind=SubjectKind.WORK)),
        "work_b": make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r3-work-b", sub_kind=SubjectKind.WORK)),
        "project_a": make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r3-project-a", sub_kind=SubjectKind.PROJECT)),
        "project_b": make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r3-project-b", sub_kind=SubjectKind.PROJECT)),
        "milestone_a": make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r3-milestone-a", sub_kind=SubjectKind.PLAN)),
        "milestone_b": make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "r3-milestone-b", sub_kind=SubjectKind.PLAN)),
    }
    workflow_id = make_id(IdKind.WORKFLOW, "r3-review-workflow")
    repo = InMemoryGraphRepository()
    repo.store(records.workflow(workflow_id, semantic_intent="M4-2 R3 review", creation_context={}))
    subject_kinds = {
        "work_a": "WorkSubject",
        "work_b": "WorkSubject",
        "project_a": "ProjectSubject",
        "project_b": "ProjectSubject",
        "milestone_a": "PlanSubject",
        "milestone_b": "PlanSubject",
    }
    for name, ref in refs.items():
        repo.store(
            records.subject(
                ref.internal_id,
                kind=subject_kinds[name],
                workflow_ref=workflow_id,
                mechanical_state={"revision": 3, "state": "open"},
                id_derivation="fixture",
            )
        )
    decisions = {}
    for name, ref in refs.items():
        decision = records.decision(
            make_id(IdKind.DECISION, f"r3-decision-{name}"),
            ref.internal_id,
            "authorization",
            f"authorize {name}",
        )
        repo.store(decision)
        decisions[name] = decision
    return context, refs, repo, OwningSubjectResolver(repo), decisions


def descriptor(*, name: str = "m4_2_r3_mutation", decision_required: bool = True):
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, WRITE_ONLY

    return OperationContractDescriptor(
        name=name,
        description="independent M4-2 R3 fixture",
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
        external_authority_precondition=False,
        idempotency="same-key-same-intent-replay",
        result_contract="canonical-mutation-result",
        errors=(),
        protocol_version="1",
    )


def intent(target, *, operation: str, key: str = "r3-intent"):
    from aota_forge.core.contracts.mutation import MutationIntent

    return MutationIntent(
        operation=operation,
        semantic_inputs={"value": "independent"},
        logical_target=target.to_canonical(),
        mutation_scope={"mode": "write"},
        idempotency_key=key,
    )


def evidence_for(decision, target, operation: str, *, scope=None):
    from aota_forge.core.authority import MaterializedDecisionEvidence

    return MaterializedDecisionEvidence.from_decision(
        decision,
        operation=operation,
        target=target,
        expected_revision=3,
        scope=scope or {"mode": "write"},
    )


def authorization(context, target, contract, *, basis="materialized_decision_evidence", decision=None, authorization_id="r3-auth", reservation_ref=None):
    from aota_forge.core.authority import TrustedMutationAuthorization

    current_intent = intent(target, operation=contract.name)
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
        decision_basis=decision,
        authorization_id=authorization_id,
        reservation_ref=reservation_ref,
        trusted_context=context,
    )


def issue(auth, contract, context, target, engine, *, now=NOW):
    from aota_forge.core.authorization import CapabilityLeaseIssuer

    return CapabilityLeaseIssuer(engine).issue(
        auth,
        contract,
        intent=intent(target, operation=contract.name),
        trusted_context=context,
        now=now,
    )


def attempt_with_resolved_decision(repo, decision_record, original_decision, call):
    repo.store(decision_record)
    try:
        return attempt(call)
    finally:
        repo.store(original_decision)


def expect_rejection_with_resolved_decision(repo, decision_record, original_decision, call, code: str) -> bool:
    result, detail = attempt_with_resolved_decision(repo, decision_record, original_decision, call)
    return result == "reject" and detail == code


def record_recomputed(name: str, auth, contract, context, target, engine, category: str, *, repo=None, resolved_decision=None, original_decision=None) -> tuple[str, str]:
    if resolved_decision is not None:
        result, detail = attempt_with_resolved_decision(
            repo,
            resolved_decision,
            original_decision,
            lambda: issue(auth, contract, context, target, engine),
        )
    else:
        result, detail = attempt(lambda: issue(auth, contract, context, target, engine))
    RECOMPUTED_RESULTS.append((name, result, category))
    expected_reject = category in {"semantic", "digest"}
    passed = result == ("reject" if expected_reject else "accept")
    check(name, passed, f"{result}:{detail}")
    return result, detail


def source_scope_review() -> tuple[bool, int, int, int, int]:
    changed = subprocess.check_output(
        ["git", "diff", "--name-only", COMMON_SOURCE_BASE, TARGET_SHA],
        cwd=ROOT,
        text=True,
    ).splitlines()
    source = [path for path in changed if path.startswith("aota_forge/")]
    m4_2 = {"aota_forge/core/authority.py", "aota_forge/core/capability_lease.py", "aota_forge/core/authorization.py"}
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
    unexpected = [path for path in source if path not in m4_2]
    return not unexpected, len(set(source) & m4_4), len(set(source) & shared), len(set(source) & integration), len(unexpected)


def run_clean_source_guard() -> tuple[bool, str]:
    temp = Path(tempfile.mkdtemp(prefix="m4-2-r3-guard-", dir="/tmp/opencode"))
    try:
        added = subprocess.run(
            ["git", "worktree", "add", "--detach", str(temp), TARGET_SHA],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if added.returncode != 0:
            return False, added.stdout + added.stderr
        result = subprocess.run(
            [sys.executable, str(temp / "scripts" / "m4_2_source_guard.py")],
            cwd=temp,
            check=False,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(temp)], cwd=ROOT, check=False)
        shutil.rmtree(temp, ignore_errors=True)


def main() -> int:
    from aota_forge.core.authorization import (
        AuthorizationErrorCode,
        CapabilityLeaseIssuer,
        LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER,
        MATERIALIZED_DECISION_VALIDATION_BYPASS_ALLOWED,
    )
    from aota_forge.core.authority import (
        APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE,
        AuthorityEngine,
        DECISION_DIGEST_VALIDITY_EQUALS_SEMANTIC_VALIDITY,
        MATERIALIZED_DECISION_VALIDATION_BYPASS_ALLOWED as AUTHORITY_VALIDATION_BYPASS,
        RESOLVER_IS_SEMANTIC_DECISION_MAKER,
    )

    target_exists = subprocess.run(
        ["git", "cat-file", "-e", f"{TARGET_SHA}^{{commit}}"], cwd=ROOT, check=False
    ).returncode == 0
    target_ref = subprocess.check_output(["git", "rev-parse", SOURCE_REF], cwd=ROOT, text=True).strip()
    target_remote = subprocess.check_output(
        ["git", "rev-parse", f"refs/remotes/origin/aota/m4/m4-2-source"], cwd=ROOT, text=True
    ).strip()
    ancestry = (
        subprocess.run(["git", "merge-base", "--is-ancestor", COMMON_SOURCE_BASE, TARGET_SHA], cwd=ROOT, check=False).returncode == 0
        and subprocess.run(["git", "merge-base", "--is-ancestor", "44150acf551edf8b77101eb5c64d74d090f9a7b5", "299a146a1d04086d6d7770a4ce55e7569c760922"], cwd=ROOT, check=False).returncode == 0
        and subprocess.run(["git", "merge-base", "--is-ancestor", "299a146a1d04086d6d7770a4ce55e7569c760922", TARGET_SHA], cwd=ROOT, check=False).returncode == 0
    )
    check("TARGET_SHA_VERIFIED", target_exists and subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == TARGET_SHA)
    check("TARGET_REMOTE_REF_VERIFIED", target_ref == TARGET_SHA and target_remote == TARGET_SHA)
    check("TARGET_ANCESTRY_VERIFIED", ancestry)
    check("ACCEPTED_PLAN_SHA_VERIFIED", subprocess.run(["git", "cat-file", "-e", "8458c0150bd9ec96e35f1f4059e684908fe67469^{commit}"], cwd=ROOT, check=False).returncode == 0)

    old_ok, repaired_ok, old_detail, repaired_detail = verify_old_and_repaired_exact_case()
    check("FAILED_R2_ADVERSARIAL_CASE_REPRODUCED_ON_OLD_SHA", old_ok, old_detail)
    check("FAILED_R2_ADVERSARIAL_CASE_REJECTED_ON_REPAIRED_SHA", repaired_ok, repaired_detail)

    scope_ok, m4_4_count, shared_count, integration_count, forbidden_count = source_scope_review()
    check("SOURCE_REPAIR_DIFF_SCOPE_REVIEW", scope_ok, f"forbidden={forbidden_count}")
    check("ALL_SOURCE_WRITES_WITHIN_M4_2_EXCLUSIVE_PATHS", scope_ok)
    check("M4_4_EXCLUSIVE_WRITE_PATH_MUTATION_COUNT", m4_4_count == 0, str(m4_4_count))
    check("SHARED_READ_ONLY_PATH_MUTATION_COUNT", shared_count == 0, str(shared_count))
    check("INTEGRATION_ONLY_PATH_MUTATION_COUNT", integration_count == 0, str(integration_count))
    check("FORBIDDEN_CROSS_LANE_WRITE_COUNT", forbidden_count == 0, str(forbidden_count))

    context, refs, repo, resolver, decisions = fixture()
    engine = AuthorityEngine(resolver)
    contract = descriptor()
    valid = evidence_for(decisions["work_a"], refs["work_a"], contract.name)
    valid_auth = authorization(context, refs["work_a"], contract, decision=valid)
    valid_result, valid_detail = attempt(lambda: issue(valid_auth, contract, context, refs["work_a"], engine))
    check("VALID_BASELINE_ACCEPTED", valid_result == "accept", f"{valid_result}:{valid_detail}")
    RECOMPUTED_RESULTS.append(("A1_VALID_BASELINE", valid_result, "valid"))

    record_recomputed(
        "A2_RECOMPUTED_MISSING_STATEMENT_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(replace(decisions["work_a"], statement=""), refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
        repo=repo,
        resolved_decision=replace(decisions["work_a"], statement=""),
        original_decision=decisions["work_a"],
    )
    wrong_field_type = replace(decisions["work_a"], statement=123)
    record_recomputed(
        "A3_RECOMPUTED_WRONG_FIELD_TYPE_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(wrong_field_type, refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
        repo=repo,
        resolved_decision=wrong_field_type,
        original_decision=decisions["work_a"],
    )
    record_recomputed(
        "A4_RECOMPUTED_WRONG_OPERATION_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(decisions["work_a"], refs["work_a"], "wrong_operation")),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
    )
    record_recomputed(
        "A5_RECOMPUTED_WRONG_TARGET_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(decisions["work_a"], refs["work_b"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
    )
    record_recomputed(
        "A6_RECOMPUTED_WRONG_SCOPE_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(decisions["work_a"], refs["work_a"], contract.name, scope={"mode": "other"})),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
    )

    project_contract = descriptor(name="m4_2_r3_project_mutation")
    project_valid = evidence_for(decisions["project_a"], refs["project_a"], project_contract.name)
    project_auth = authorization(
        context,
        refs["project_a"],
        project_contract,
        basis="project_milestone_semantic_decision",
        decision=project_valid,
    )
    record_recomputed(
        "A7_RECOMPUTED_WRONG_PROJECT_REJECTED",
        replace(project_auth, decision_basis=evidence_for(decisions["project_b"], refs["project_b"], project_contract.name)),
        project_contract,
        context,
        refs["project_a"],
        engine,
        "semantic",
    )
    milestone_valid = evidence_for(decisions["milestone_a"], refs["milestone_a"], project_contract.name)
    milestone_auth = authorization(
        context,
        refs["milestone_a"],
        project_contract,
        basis="project_milestone_semantic_decision",
        decision=milestone_valid,
    )
    record_recomputed(
        "A8_RECOMPUTED_WRONG_MILESTONE_REJECTED",
        replace(milestone_auth, decision_basis=evidence_for(decisions["milestone_b"], refs["milestone_b"], project_contract.name)),
        project_contract,
        context,
        refs["milestone_a"],
        engine,
        "semantic",
    )
    record_recomputed(
        "A9_RECOMPUTED_WRONG_SUBJECT_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(decisions["work_b"], refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
    )
    wrong_kind = replace(decisions["work_a"], decision_kind="")
    record_recomputed(
        "A10_RECOMPUTED_INVALID_DECISION_KIND_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(wrong_kind, refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
        repo=repo,
        resolved_decision=wrong_kind,
        original_decision=decisions["work_a"],
    )
    malformed_nested_target = replace(decisions["work_a"], target_refs=[{"malformed": True}])
    record_recomputed(
        "A11_RECOMPUTED_MALFORMED_NESTED_TARGET_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(malformed_nested_target, refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
        repo=repo,
        resolved_decision=malformed_nested_target,
        original_decision=decisions["work_a"],
    )
    wrong_nonempty_kind = replace(decisions["work_a"], decision_kind="review_result")
    record_recomputed(
        "A12_RECOMPUTED_WRONG_NONEMPTY_DECISION_KIND_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(wrong_nonempty_kind, refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
        repo=repo,
        resolved_decision=wrong_nonempty_kind,
        original_decision=decisions["work_a"],
    )
    wrong_nested_target = replace(decisions["work_a"], target_refs=[refs["work_b"].serialize()])
    record_recomputed(
        "A13_RECOMPUTED_WRONG_NESTED_TARGET_REFERENCE_REJECTED",
        replace(valid_auth, decision_basis=evidence_for(wrong_nested_target, refs["work_a"], contract.name)),
        contract,
        context,
        refs["work_a"],
        engine,
        "semantic",
        repo=repo,
        resolved_decision=wrong_nested_target,
        original_decision=decisions["work_a"],
    )
    record_recomputed(
        "A14_ALTERED_INTEGRITY_DIGEST_REJECTED",
        replace(valid_auth, decision_basis=replace(valid, evidence_digest="0" * 64)),
        contract,
        context,
        refs["work_a"],
        engine,
        "digest",
    )

    invalid_semantic = [row for row in RECOMPUTED_RESULTS if row[2] == "semantic"]
    invalid_semantic_rejects = sum(row[1] == "reject" for row in invalid_semantic)
    invalid_digest_rejects = sum(row[2] == "digest" and row[1] == "reject" for row in RECOMPUTED_RESULTS)
    check("DECISION_DIGEST_VALIDITY_EQUALS_SEMANTIC_VALIDITY=no", not DECISION_DIGEST_VALIDITY_EQUALS_SEMANTIC_VALIDITY)
    check("RESOLVED_DECISION_STRUCTURAL_VALIDATION_REVIEW", invalid_semantic_rejects >= 9)
    check("MATERIALIZED_DECISION_SEMANTIC_VALIDATION_REVIEW", invalid_semantic_rejects == len(invalid_semantic))
    check("MATERIALIZED_DECISION_TRUST_CHAIN_REVIEW", valid_result == "accept" and invalid_digest_rejects == 1)
    malformed_case_names = {
        "A2_RECOMPUTED_MISSING_STATEMENT_REJECTED",
        "A3_RECOMPUTED_WRONG_FIELD_TYPE_REJECTED",
        "A10_RECOMPUTED_INVALID_DECISION_KIND_REJECTED",
        "A11_RECOMPUTED_MALFORMED_NESTED_TARGET_REJECTED",
    }
    check(
        "MALFORMED_DECISION_WITH_VALID_DIGEST_ACCEPTED",
        not any(row[1] == "accept" for row in RECOMPUTED_RESULTS if row[0] in malformed_case_names),
    )
    check("SEMANTICALLY_INCOMPATIBLE_DECISION_WITH_VALID_DIGEST_ACCEPTED", not any(row[1] == "accept" for row in RECOMPUTED_RESULTS if row[2] == "semantic"))

    mde_code = AuthorizationErrorCode.MATERIALIZED_DECISION_REQUIRED.value
    missing_statement = replace(decisions["work_a"], statement="")
    negative_result = expect_rejection_with_resolved_decision(
        repo,
        missing_statement,
        decisions["work_a"],
        lambda: issue(replace(valid_auth, decision_basis=evidence_for(missing_statement, refs["work_a"], contract.name)), contract, context, refs["work_a"], engine),
        mde_code,
    )
    NEGATIVE_RESULTS.append(("NEG-R3-01", negative_result))
    check("NEG-R3-01", negative_result)
    negative("NEG-R3-02", lambda: issue(replace(valid_auth, decision_basis=evidence_for(decisions["work_a"], refs["work_a"], "wrong_operation")), contract, context, refs["work_a"], engine), mde_code)
    negative("NEG-R3-03", lambda: issue(replace(valid_auth, decision_basis=evidence_for(decisions["work_a"], refs["work_b"], contract.name)), contract, context, refs["work_a"], engine), mde_code)
    negative("NEG-R3-04", lambda: issue(replace(valid_auth, decision_basis=evidence_for(decisions["work_a"], refs["work_a"], contract.name, scope={"mode": "other"})), contract, context, refs["work_a"], engine), mde_code)
    negative("NEG-R3-05", lambda: issue(replace(project_auth, decision_basis=evidence_for(decisions["project_b"], refs["project_b"], project_contract.name)), project_contract, context, refs["project_a"], engine), mde_code)
    negative("NEG-R3-06", lambda: issue(replace(milestone_auth, decision_basis=evidence_for(decisions["milestone_b"], refs["milestone_b"], project_contract.name)), project_contract, context, refs["milestone_a"], engine), mde_code)
    negative("NEG-R3-07", lambda: issue(replace(valid_auth, decision_basis=evidence_for(decisions["work_b"], refs["work_a"], contract.name)), contract, context, refs["work_a"], engine), mde_code)
    negative_result = expect_rejection_with_resolved_decision(
        repo,
        wrong_kind,
        decisions["work_a"],
        lambda: issue(replace(valid_auth, decision_basis=evidence_for(wrong_kind, refs["work_a"], contract.name)), contract, context, refs["work_a"], engine),
        mde_code,
    )
    NEGATIVE_RESULTS.append(("NEG-R3-08", negative_result))
    check("NEG-R3-08", negative_result)
    negative("NEG-R3-09", lambda: issue(replace(valid_auth, decision_basis=None), contract, context, refs["work_a"], engine), mde_code)
    negative("NEG-R3-10", lambda: issue(valid_auth, contract, context, refs["work_a"], None), mde_code)
    project_only = authorization(context, refs["project_a"], project_contract, basis="project_milestone_semantic_decision", decision=None, authorization_id="arbitrary-r3-auth")
    negative("NEG-R3-11", lambda: issue(project_only, project_contract, context, refs["project_a"], engine), mde_code)
    reservation_only = replace(project_only, authorization_id=None, reservation_ref="arbitrary-r3-reservation")
    negative("NEG-R3-12", lambda: issue(reservation_only, project_contract, context, refs["project_a"], engine), mde_code)
    negative_result = expect_rejection_with_resolved_decision(
        repo,
        wrong_field_type,
        decisions["work_a"],
        lambda: issue(replace(valid_auth, decision_basis=evidence_for(wrong_field_type, refs["work_a"], contract.name)), contract, context, refs["work_a"], engine),
        mde_code,
    )
    NEGATIVE_RESULTS.append(("NEG-R3-13", negative_result))
    check("NEG-R3-13", negative_result)
    from aota_forge.core import authorization as authorization_module

    changed = set(subprocess.check_output(["git", "diff", "--name-only", COMMON_SOURCE_BASE, TARGET_SHA], cwd=ROOT, text=True).splitlines())
    negative_source = (
        not authorization_module.M4_2_WRITE_INGRESS_IMPLEMENTED
        and not authorization_module.M4_2_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED
        and not authorization_module.M4_2_DURABLE_JOURNAL_IMPLEMENTED
        and "aota_forge/core/ingress.py" not in changed
    )
    NEGATIVE_RESULTS.append(("NEG-R3-14", negative_source))
    check("NEG-R3-14", negative_source)

    unresolved = replace(valid, decision_ref=replace(valid.decision_ref, internal_id=replace(valid.decision_ref.internal_id, value="r3-missing-decision")))
    missing_result, missing_detail = attempt(lambda: issue(replace(valid_auth, decision_basis=unresolved), contract, context, refs["work_a"], engine))
    check("RESOLVER_MISSING_OBJECT_REJECTED", missing_result == "reject" and missing_detail == mde_code)

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

    malformed_resolver_engine = AuthorityEngine(ObjectReturningResolver(resolver))
    resolver_result, resolver_detail = attempt(lambda: issue(valid_auth, contract, context, refs["work_a"], malformed_resolver_engine))
    check("RESOLVER_RETURNED_OBJECT_EQUALS_VALID_DECISION", resolver_result == "reject" and resolver_detail == mde_code)
    check("RESOLVER_IS_SEMANTIC_DECISION_MAKER=no", not RESOLVER_IS_SEMANTIC_DECISION_MAKER)
    missing_engine_result, missing_engine_detail = attempt(lambda: CapabilityLeaseIssuer().issue(valid_auth, contract, intent=intent(refs["work_a"], operation=contract.name), trusted_context=context, now=NOW))
    check("NO_AUTHORITY_ENGINE_REQUIRED_DECISION_FAILS_CLOSED", missing_engine_result == "reject" and missing_engine_detail == mde_code)
    check(
        "MATERIALIZED_DECISION_VALIDATION_BYPASS_ALLOWED=no",
        not MATERIALIZED_DECISION_VALIDATION_BYPASS_ALLOWED and not AUTHORITY_VALIDATION_BYPASS,
    )
    check("LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER=no", not LEASE_ISSUER_IS_SEMANTIC_DECISION_MAKER)
    check("ARBITRARY_AUTHORIZATION_IDENTIFIER_IS_SEMANTIC_AUTHORITY=no", all(ok for name, ok in NEGATIVE_RESULTS if name == "NEG-R3-11"))
    check("ARBITRARY_RESERVATION_IDENTIFIER_IS_SEMANTIC_AUTHORITY=no", all(ok for name, ok in NEGATIVE_RESULTS if name == "NEG-R3-12"))
    check("APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE=no", not APPROVAL_EVIDENCE_EQUALS_MATERIALIZED_DECISION_EVIDENCE)

    source_guard_ok, source_guard_output = run_clean_source_guard()
    source_guard_path = ROOT / "scripts" / "m4_2_source_guard.py"
    source_guard_text = source_guard_path.read_text(encoding="ascii")
    recomputed_helper = source_guard_text.split("def expect_recomputed_rejection", 1)[1].split("decision_cases", 1)[0]
    source_guard_real_case = "decision_repo.store" in recomputed_helper
    check(
        "SOURCE_GUARD_SELF_CONSISTENCY",
        source_guard_ok
        and "FOCUSED_TEST_COUNT=35" in source_guard_output
        and "FOCUSED_TEST_PASSED=35" in source_guard_output
        and "RECOMPUTED_DIGEST_ADVERSARIAL_CASE_COUNT=11" in source_guard_output
        and "RECOMPUTED_DIGEST_INVALID_CASE_REJECT_COUNT=10" in source_guard_output
        and source_guard_real_case,
    )
    check("SOURCE_GUARD_REAL_MALFORMED_DECISION_RECOMPUTED_DIGEST_CASE", source_guard_real_case)
    check("SOURCE_GUARD_REAL_PATH_USES_ISSUER", "CapabilityLeaseIssuer(decision_engine)" in source_guard_text and "_issue(" in source_guard_text)
    check("TAUTOLOGICAL_VALIDATION_DETECTED", "json.load" not in source_guard_text and ".json" not in source_guard_text and TARGET_SHA not in source_guard_text)
    check("TEST_CASE_NAME_SPECIAL_CASING_DETECTED", re.search(r"if\s+name\s*(?:==|in)", source_guard_text) is None)
    check("EVIDENCE_SELF_VALIDATION_AS_TEST", "json.load" not in source_guard_text and ".json" not in source_guard_text)
    check("GUARD_DESCENDANT_AWARE", "merge-base" in source_guard_text and "--is-ancestor" in source_guard_text)
    check("GUARD_HEAD_EQUALS_COMMON_BASE_REQUIRED", "HEAD == COMMON_SOURCE_BASE" not in source_guard_text)

    print(f"RECOMPUTED_DIGEST_ADVERSARIAL_CASE_COUNT={len(RECOMPUTED_RESULTS)}")
    print(f"VALID_BASELINE_CASE_COUNT={sum(row[2] == 'valid' for row in RECOMPUTED_RESULTS)}")
    print(f"INVALID_SEMANTIC_CASE_COUNT={len(invalid_semantic)}")
    print(f"INVALID_SEMANTIC_CASE_REJECT_COUNT={invalid_semantic_rejects}")
    print(f"INVALID_DIGEST_CASE_REJECT_COUNT={invalid_digest_rejects}")
    print(f"INDEPENDENT_NEGATIVE_CASE_COUNT={len(NEGATIVE_RESULTS)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_REJECT_COUNT={sum(ok for _, ok in NEGATIVE_RESULTS)}")
    print(f"INDEPENDENT_NEGATIVE_CASE_UNEXPECTED_ACCEPT_COUNT={len(NEGATIVE_RESULTS) - sum(ok for _, ok in NEGATIVE_RESULTS)}")
    failed = [name for name, passed, _ in RESULTS if not passed]
    print(f"INDEPENDENT_R3_GUARD={'PASS' if not failed else 'FAIL'}")
    if failed:
        print("FAILED_CHECKS=" + ",".join(failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
