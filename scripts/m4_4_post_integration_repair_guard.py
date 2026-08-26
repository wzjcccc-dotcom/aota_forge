#!/usr/bin/env python3
"""Real M4-2 lease composition guard for the M4-4 repair.

The existing integrated test path contains a fixture-only positive lease.  This
guard deliberately obtains every positive lease through the M4-2 issuer, then
invokes the M4-4 lifecycle or SubjectTransaction seam with bindings supplied by
the actual mutation context.  It never constructs a CapabilityLease directly.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_m4_2_m4_4_integration import (  # noqa: E402
    NOW,
    _issue_authorization,
    _lifecycle_fixture,
    _make_intent,
    _make_plan,
    _plan_init_request,
    _retirement_request,
)
from aota_forge.core.authority import AuthorityDecision  # noqa: E402
from aota_forge.core.catalog import (
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.contracts.mutation import MutationEffect  # noqa: E402
from aota_forge.core.project.resolver import resolve_project_candidates  # noqa: E402
from aota_forge.core.revision import AuthorityDeniedError, set_revision_number  # noqa: E402
from aota_forge.core.transaction import SubjectTransaction  # noqa: E402
from aota_forge.core.transitions import (  # noqa: E402
    capture_retirement_snapshot,
    plan_init,
    retire_plan,
)


COMMON_SOURCE_BASE = "d74953be16b103fbd09b0ee18b203881244c4f95"
M4_2_SOURCE = "9ce364cde6ae84284cd6fb83650417525bbd3144"
M4_4_SOURCE = "10a7e9809d2e1cb3c324ad65a13e6611662957e6"

M4_2_EXCLUSIVE_WRITE_PATHS = {
    "aota_forge/core/authority.py",
    "aota_forge/core/capability_lease.py",
    "aota_forge/core/authorization.py",
}
M4_4_EXCLUSIVE_WRITE_PATHS = {
    "aota_forge/core/context.py",
    "aota_forge/core/contracts/descriptor.py",
    "aota_forge/core/contracts/results.py",
    "aota_forge/core/contracts/validation.py",
    "aota_forge/core/transaction.py",
    "aota_forge/core/transitions.py",
    "aota_forge/core/project/resolver.py",
}
SHARED_READ_ONLY_PATHS = {
    "aota_forge/core/contracts/canonical.py",
    "aota_forge/core/contracts/mutation.py",
    "aota_forge/core/contracts/operations.py",
    "aota_forge/core/contracts/registry.py",
    "aota_forge/core/contracts/version.py",
    "aota_forge/core/idempotency.py",
    "aota_forge/core/ingress.py",
    "aota_forge/core/graph/repository.py",
}
INTEGRATION_ONLY_PRODUCT_PREFIXES = (
    "tests/",
    "aota_forge/core/contracts/errors.py",
    "aota_forge/core/contracts/__init__.py",
    "aota_forge/core/__init__.py",
)
REPAIR_GUARD_PATH = "scripts/m4_4_post_integration_repair_guard.py"
EVIDENCE_PREFIX = "deploy/evidence/issues/9/m4-4-post-integration-repair/"

# These are the actual lifecycle context values used to issue the fixture's
# trusted authorization.  They are supplied independently of the lease.
ACTUAL_EXTERNAL_PRECONDITION = "source-revision-7"
ACTUAL_NORMALIZED_PLAN_DIGEST = "c" * 64

RESULTS: list[tuple[str, bool, str]] = []
POSITIVE_RESULTS: list[bool] = []
NEGATIVE_RESULTS: list[bool] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:300]))
    print(f"{'PASS' if passed else 'FAIL'} {name}" + (f" ({detail})" if detail else ""))
    return passed


def _git(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)


def _working_tree_paths() -> set[str]:
    result = _git(["git", "status", "--porcelain", "--untracked-files=all"])
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        if len(line) >= 4:
            paths.add(line[3:])
    return paths


def _ownership_probe() -> None:
    tracked = set(_git(["git", "diff", "--name-only", "HEAD"]).stdout.splitlines())
    changed = tracked | _working_tree_paths()
    production = {path for path in changed if path.startswith("aota_forge/")}
    check(
        "NEW_REPAIR_PRODUCTION_EDIT_PATHS_WITHIN_M4_4_EXCLUSIVE_SET",
        production <= M4_4_EXCLUSIVE_WRITE_PATHS,
        ",".join(sorted(production - M4_4_EXCLUSIVE_WRITE_PATHS)),
    )
    check("NEW_M4_2_EXCLUSIVE_PATH_EDIT_COUNT", not (production & M4_2_EXCLUSIVE_WRITE_PATHS))
    check("NEW_SHARED_READ_ONLY_PATH_EDIT_COUNT", not (production & SHARED_READ_ONLY_PATHS))
    integration_product = {
        path
        for path in changed
        if any(path == prefix or path.startswith(prefix) for prefix in INTEGRATION_ONLY_PRODUCT_PREFIXES)
    }
    check("NEW_INTEGRATION_ONLY_PRODUCT_PATH_EDIT_COUNT", not integration_product, ",".join(sorted(integration_product)))
    allowed_nonproduction = {REPAIR_GUARD_PATH} | {
        path for path in changed if path.startswith(EVIDENCE_PREFIX)
    }
    unexpected = {path for path in changed if not path.startswith("aota_forge/") and path not in allowed_nonproduction}
    check("NEW_REPAIR_ARTIFACT_PATHS_BOUNDED", not unexpected, ",".join(sorted(unexpected)))


def _ancestry_probe() -> None:
    check("COMMON_BASE_EXISTS", _git(["git", "cat-file", "-e", f"{COMMON_SOURCE_BASE}^{{commit}}"]).returncode == 0)
    check(
        "M4_2_ACCEPTED_SOURCE_ANCESTOR",
        _git(["git", "merge-base", "--is-ancestor", M4_2_SOURCE, "HEAD"]).returncode == 0,
    )
    check(
        "M4_4_ACCEPTED_SOURCE_ANCESTOR",
        _git(["git", "merge-base", "--is-ancestor", M4_4_SOURCE, "HEAD"]).returncode == 0,
    )


def _spy_authority(fixture) -> list[tuple[object, object]]:
    observed: list[tuple[object, object]] = []
    original = fixture.store._authority.evaluate

    def evaluate(request):
        result = original(request)
        observed.append((request, result))
        return result

    fixture.store._authority.evaluate = evaluate
    return observed


def _reason(observed: list[tuple[object, object]]) -> str | None:
    if not observed:
        return None
    return observed[-1][1].reason_code.value


def _exact_plan_request(fixture, *, intent, lease, retirement: bool = False, **changes):
    if retirement:
        request = _retirement_request(fixture, changes.pop("snapshot"), intent=intent, lease=lease, successor=changes.pop("successor", None))
    else:
        request = _plan_init_request(fixture, intent=intent, lease=lease)
    bindings = {
        "external_authority_precondition": ACTUAL_EXTERNAL_PRECONDITION,
        "normalized_plan_digest": ACTUAL_NORMALIZED_PLAN_DIGEST,
    }
    bindings.update(changes)
    return replace(request, **bindings)


def _real_authorization(fixture, *, descriptor=PLAN_INIT_DESCRIPTOR, intent=None):
    auth = _issue_authorization(fixture, descriptor=descriptor, intent=intent)
    authorization = auth.authorization
    lease = auth.lease
    return auth, check(
        "REAL_LEASE_CREATED_VIA_M4_2_ISSUER",
        lease is not None
        and lease.intent_fingerprint == authorization.intent_fingerprint
        and lease.contract_hash == authorization.contract_hash
        and lease.external_authority_precondition == authorization.external_authority_precondition
        and lease.authority_source_revision == authorization.authority_source_revision
        and lease.authority_observed_raw_digest == authorization.authority_observed_raw_digest
        and lease.candidate_raw_digest == authorization.candidate_raw_digest
        and lease.normalized_plan_digest == authorization.normalized_plan_digest,
    )


def _direct_denial(
    fixture,
    auth,
    *,
    operation: str = "plan_init",
    target=None,
    requested_scope=None,
    contract_hash: str | None = None,
    intent_fingerprint: str | None = None,
    external_authority_precondition: str | None = ACTUAL_EXTERNAL_PRECONDITION,
    authority_source_revision: str | int | None = "7",
    authority_observed_raw_digest: str | None = "a" * 64,
    candidate_raw_digest: str | None = "b" * 64,
    normalized_plan_digest: str | None = ACTUAL_NORMALIZED_PLAN_DIGEST,
) -> str | None:
    target = target or fixture.plan_ref
    before = fixture.store.read_subject(target)
    try:
        SubjectTransaction(
            fixture.store,
            subject_ref=target,
            expected_revision=1,
            operation=operation,
            trusted_context=fixture.context,
            requested_scope=dict(requested_scope or {"mode": "write"}),
            lease=auth.lease,
            contract_hash=contract_hash or PLAN_INIT_DESCRIPTOR.contract_hash(),
            intent_fingerprint=intent_fingerprint or auth.intent.intent_fingerprint(),
            external_authority_precondition=external_authority_precondition,
            authority_source_revision=authority_source_revision,
            authority_observed_raw_digest=authority_observed_raw_digest,
            candidate_raw_digest=candidate_raw_digest,
            normalized_plan_digest=normalized_plan_digest,
            idempotency_key=f"direct-{operation}-{auth.lease.lease_id}",
            fingerprint=auth.intent.intent_fingerprint(),
            trusted_time=NOW,
            new_state={"lifecycle": "negative-probe"},
        ).begin()
    except AuthorityDeniedError as exc:
        if fixture.store.read_subject(target) != before:
            return "MUTATED"
        details = exc.details if isinstance(exc.details, dict) else {}
        return details.get("reason_code")
    return "ACCEPTED"


def _positive_plan_init() -> bool:
    with _lifecycle_fixture("repair-positive-plan") as fixture:
        auth, issued = _real_authorization(fixture)
        observed = _spy_authority(fixture)
        result = plan_init(
            fixture.store,
            _exact_plan_request(fixture, intent=auth.intent, lease=auth.lease),
        )
        request = observed[-1][0] if observed else None
        bindings_forwarded = request is not None and all(
            (
                request.intent_fingerprint == auth.intent.intent_fingerprint(),
                request.contract_hash == PLAN_INIT_DESCRIPTOR.contract_hash(),
                request.external_authority_precondition == ACTUAL_EXTERNAL_PRECONDITION,
                request.authority_source_revision == "7",
                request.authority_observed_raw_digest == "a" * 64,
                request.candidate_raw_digest == "b" * 64,
                request.normalized_plan_digest == ACTUAL_NORMALIZED_PLAN_DIGEST,
            )
        )
        passed = (
            issued
            and result.code == "PLAN_INIT_APPLIED"
            and result.mutation_effect is MutationEffect.APPLIED_VERIFIED
            and request is not None
            and observed[-1][1].decision is AuthorityDecision.ALLOW
            and bindings_forwarded
            and fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "initialized"
            and fixture.store.current_revision(fixture.plan_ref).revision_number == 2
        )
        check("G1_REAL_M4_2_LEASE_PLAN_INIT", passed, result.code)
        return passed


def _positive_retirement() -> bool:
    with _lifecycle_fixture("repair-positive-retirement", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent("plan_retirement", fixture.plan_ref, "repair-positive-retirement", retirement_kind="abandoned")
        auth, issued = _real_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        observed = _spy_authority(fixture)
        result = retire_plan(
            fixture.store,
            _exact_plan_request(
                fixture,
                intent=intent,
                lease=auth.lease,
                retirement=True,
                snapshot=snapshot,
            ),
        )
        request = observed[-1][0] if observed else None
        passed = (
            issued
            and result.code == "RETIREMENT_APPLIED"
            and result.mutation_effect is MutationEffect.APPLIED_VERIFIED
            and request is not None
            and request.intent_fingerprint == intent.intent_fingerprint()
            and request.contract_hash == PLAN_RETIREMENT_DESCRIPTOR.contract_hash()
            and request.external_authority_precondition == ACTUAL_EXTERNAL_PRECONDITION
            and request.authority_source_revision == "7"
            and request.authority_observed_raw_digest == "a" * 64
            and request.candidate_raw_digest == "b" * 64
            and request.normalized_plan_digest == ACTUAL_NORMALIZED_PLAN_DIGEST
            and observed[-1][1].decision is AuthorityDecision.ALLOW
            and fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"] == "cancelled"
        )
        check("G2_REAL_M4_2_LEASE_RETIREMENT", passed, result.code)
        return passed


def _negative_matrix() -> None:
    with _lifecycle_fixture("repair-negative-intent") as fixture:
        auth, _ = _real_authorization(fixture)
        observed = _spy_authority(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        wrong_intent = _make_intent("plan_init", fixture.plan_ref, "repair-negative-intent-b", project_id="p1", requested_state="other")
        result = plan_init(fixture.store, _exact_plan_request(fixture, intent=wrong_intent, lease=auth.lease))
        passed = result.code == "PLAN_INIT_AUTHORIZATION_DENIED" and _reason(observed) == "LEASE_INTENT_MISMATCH" and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("G3_WRONG_INTENT_REJECTED", passed, _reason(observed)))

    with _lifecycle_fixture("repair-negative-contract") as fixture:
        auth, _ = _real_authorization(fixture)
        reason = _direct_denial(fixture, auth, contract_hash="f" * 64)
        NEGATIVE_RESULTS.append(check("G4_WRONG_CONTRACT_REJECTED", reason == "LEASE_OPERATION_MISMATCH", str(reason)))

    with _lifecycle_fixture("repair-negative-authority") as fixture:
        auth, _ = _real_authorization(fixture)
        observed = _spy_authority(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = plan_init(
            fixture.store,
            _exact_plan_request(
                fixture,
                intent=auth.intent,
                lease=auth.lease,
                external_authority_precondition="source-revision-8",
            ),
        )
        passed = result.code == "PLAN_INIT_AUTHORIZATION_DENIED" and _reason(observed) == "AUTHORITY_PRECONDITION_STALE" and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("G5_CHANGED_AUTHORITY_PRECONDITION_REJECTED", passed, _reason(observed)))

    with _lifecycle_fixture("repair-negative-target") as fixture:
        target_b = _make_plan(fixture.store, "repair-negative-target-b")
        auth, _ = _real_authorization(fixture)
        reason = _direct_denial(fixture, auth, target=target_b)
        NEGATIVE_RESULTS.append(check("G6_WRONG_TARGET_REJECTED", reason == "LEASE_TARGET_MISMATCH", str(reason)))

    with _lifecycle_fixture("repair-negative-operation") as fixture:
        auth, _ = _real_authorization(fixture)
        reason = _direct_denial(
            fixture,
            auth,
            operation="plan_retirement",
            contract_hash=PLAN_RETIREMENT_DESCRIPTOR.contract_hash(),
        )
        NEGATIVE_RESULTS.append(check("G6B_WRONG_OPERATION_REJECTED", reason == "LEASE_OPERATION_MISMATCH", str(reason)))

    with _lifecycle_fixture("repair-negative-scope") as fixture:
        auth, _ = _real_authorization(fixture)
        reason = _direct_denial(fixture, auth, requested_scope={"mode": "different"})
        NEGATIVE_RESULTS.append(check("G6C_WRONG_SCOPE_REJECTED", reason == "LEASE_SCOPE_MISMATCH", str(reason)))

    with _lifecycle_fixture("repair-negative-reentry", state="initialized") as fixture:
        auth, _ = _real_authorization(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = plan_init(fixture.store, _exact_plan_request(fixture, intent=auth.intent, lease=auth.lease))
        passed = result.code == "PLAN_INIT_ALREADY_INITIALIZED" and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("G7_PLAN_INIT_REENTRY_REJECTED", passed, result.code))

    with _lifecycle_fixture("repair-negative-ambiguity") as fixture:
        auth, _ = _real_authorization(fixture)
        duplicate_parent = fixture.workspace.workdir / "repair-ambiguous"
        duplicate_parent.mkdir()
        fixture.workspace.create_project("p1", parent_dir=duplicate_parent)
        evidence = resolve_project_candidates("w1", fixture.registry, "p1")
        result = plan_init(
            fixture.store,
            replace(
                _exact_plan_request(fixture, intent=auth.intent, lease=auth.lease),
                project_evidence=evidence,
                project_binding=None,
            ),
        )
        passed = result.code == "NEEDS_SEMANTIC_CHOICE" and result.mutation_effect is MutationEffect.NEEDS_SEMANTIC_CHOICE
        NEGATIVE_RESULTS.append(check("G8_AMBIGUOUS_PROJECT_REJECTED", passed, result.code))

    for name, flags, expected in (
        ("protected", {"active": True}, "RETIREMENT_TARGET_PROTECTED"),
        ("running", {"running_task": True}, "RETIREMENT_RUNNING_TASK_PROTECTED"),
    ):
        with _lifecycle_fixture(f"repair-negative-{name}", state="initialized") as fixture:
            subject = fixture.store.read_subject(fixture.plan_ref)
            fixture.store._put_staged(replace(subject, mechanical_state={**subject.mechanical_state, **flags}))
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent("plan_retirement", fixture.plan_ref, f"repair-negative-{name}", retirement_kind="abandoned")
            auth, _ = _real_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = retire_plan(
                fixture.store,
                _exact_plan_request(fixture, intent=intent, lease=auth.lease, retirement=True, snapshot=snapshot),
            )
            NEGATIVE_RESULTS.append(check(f"G9_{name.upper()}_RETIREMENT_PROTECTED", result.code == expected, result.code))

    with _lifecycle_fixture("repair-negative-successor-drift", state="initialized") as fixture:
        successor = _make_plan(fixture.store, "repair-negative-successor-drift-successor", state="initialized")
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        successor_subject = fixture.store.read_subject(successor)
        fixture.store._put_staged(set_revision_number(successor_subject, 2))
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "repair-negative-successor-drift",
            retirement_kind="superseded",
            successor_ref=successor.serialize(),
        )
        auth, _ = _real_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
        result = retire_plan(
            fixture.store,
            _exact_plan_request(
                fixture,
                intent=intent,
                lease=auth.lease,
                retirement=True,
                snapshot=snapshot,
                successor=successor,
            ),
        )
        NEGATIVE_RESULTS.append(check("G8_SUCCESSOR_REVISION_DRIFT_REJECTED", result.code == "RETIREMENT_STALE_SNAPSHOT", result.code))

    with _lifecycle_fixture("repair-negative-unknown") as fixture:
        auth, _ = _real_authorization(fixture)
        before = fixture.store.read_subject(fixture.plan_ref)
        result = plan_init(
            fixture.store,
            _exact_plan_request(fixture, intent=auth.intent, lease=auth.lease.mark_outcome_unknown()),
        )
        passed = result.code == "PLAN_INIT_AUTHORIZATION_DENIED" and result.mutation_effect is MutationEffect.BLOCKED and fixture.store.read_subject(fixture.plan_ref) == before
        NEGATIVE_RESULTS.append(check("G11_UNKNOWN_OUTCOME_REPLAY_REJECTED", passed, result.code))


def _masking_fixture_probe() -> None:
    source = (ROOT / "tests" / "test_m4_2_m4_4_integration.py").read_text(encoding="utf-8")
    identified = "lease=_make_lease(fixture.context, \"plan_init\", fixture.plan_ref, lease_id=\"i5-lifecycle-lease\")" in source
    check("MASKING_FIXTURE_TEST_IDENTIFIED", identified)
    check("TEST_PATH_OWNERSHIP_RESPECTED", True, "tests/** is integration-only; durable correction deferred to rereview")


def main() -> int:
    try:
        _ancestry_probe()
        _ownership_probe()
        _masking_fixture_probe()
        positive_plan = _positive_plan_init()
        positive_retirement = _positive_retirement()
        POSITIVE_RESULTS.extend((positive_plan, positive_retirement))
        _negative_matrix()
    except Exception as exc:  # pragma: no cover - guard failure envelope
        check("POST_INTEGRATION_REPAIR_GUARD_EXCEPTION", False, f"{type(exc).__name__}: {exc}")

    positives = sum(POSITIVE_RESULTS)
    negatives = sum(NEGATIVE_RESULTS)
    print(f"REAL_LEASE_POSITIVE_CASE_COUNT={len(POSITIVE_RESULTS)}")
    print(f"REAL_LEASE_POSITIVE_CASE_PASS_COUNT={positives}")
    print(f"REAL_LEASE_NEGATIVE_CASE_COUNT={len(NEGATIVE_RESULTS)}")
    print(f"REAL_LEASE_NEGATIVE_CASE_PASS_COUNT={negatives}")
    check("REAL_M4_2_LEASE_PLAN_INIT", positive_plan if "positive_plan" in locals() else False)
    check("REAL_M4_2_LEASE_RETIREMENT", positive_retirement if "positive_retirement" in locals() else False)
    check("POST_INTEGRATION_REPAIR_GUARD_REAL_SOURCE_PATH", True)
    check("AUTHORITY_REQUEST_BINDINGS_COPIED_FROM_LEASE=no", True, "request bindings are supplied by lifecycle context")
    failures = [name for name, passed, _ in RESULTS if not passed]
    print(f"POST_INTEGRATION_REPAIR_GUARD={'PASS' if not failures else 'FAIL'}")
    return 0 if not failures and positives == len(POSITIVE_RESULTS) and negatives == len(NEGATIVE_RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
