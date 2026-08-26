#!/usr/bin/env python3
"""Focused M4-3 source guard.

The guard exercises the real Unified Ingress with isolated stores and a lease
issued by the existing M4-2 authority path.  It never issues a lease itself,
contacts an external authority, or writes outside the fixture store.
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

SOURCE_BASE = "16826b551b62f4874bfc6ec734cb571cb3d9dc6d"
EXCLUSIVE_SOURCE_PATHS = {
    "aota_forge/core/catalog.py",
    "aota_forge/core/handlers.py",
    "aota_forge/core/ingress.py",
}
INTEGRATION_ONLY_PATHS = {
    "scripts/m4_3_source_guard.py",
    "tests/test_m4_3_write_ingress.py",
}
EVIDENCE_PREFIX = "deploy/evidence/issues/9/m4-3-source/"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    passed = bool(condition)
    RESULTS.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _changed_paths() -> set[str]:
    changed = set(_git("diff", "--name-only", f"{SOURCE_BASE}..HEAD").splitlines())
    changed.update(_git("diff", "--name-only").splitlines())
    changed.update(_git("diff", "--cached", "--name-only").splitlines())
    for line in _git("status", "--porcelain", "--untracked-files=all").splitlines():
        if len(line) >= 4:
            path = line[3:]
            changed.add(path.split(" -> ", 1)[-1])
    return {path for path in changed if path}


def _partition_check() -> None:
    changed = _changed_paths()
    allowed = EXCLUSIVE_SOURCE_PATHS | INTEGRATION_ONLY_PATHS
    forbidden = sorted(
        path
        for path in changed
        if path not in allowed and not path.startswith(EVIDENCE_PREFIX)
    )
    check("SOURCE_PARTITION_EXACT", not forbidden, ", ".join(forbidden))


def _source_boundary_check() -> None:
    ingress_path = ROOT / "aota_forge/core/ingress.py"
    handlers_path = ROOT / "aota_forge/core/handlers.py"
    ingress = ingress_path.read_text(encoding="utf-8")
    handlers = handlers_path.read_text(encoding="utf-8")
    tree = ast.parse(ingress, filename=str(ingress_path))
    issuer_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "issue"
    ]
    external_tokens = ("subprocess", "requests", "urllib", "github", "urlopen")
    check("M4_3_RECEIVED_LEASE_ONLY_SOURCE", not issuer_calls)
    check(
        "M4_3_NO_EXTERNAL_WRITE_SOURCE",
        not any(token in ingress.lower() or token in handlers.lower() for token in external_tokens),
    )
    check(
        "M4_3_HANDLER_DISPATCH_ONLY",
        "return plan_init(request.store, request.request)" in handlers
        and "return retire_plan(request.store, request.request)" in handlers,
    )


def _bound_request(request, authorization):
    from dataclasses import replace

    return replace(
        request,
        external_authority_precondition=authorization.authorization.external_authority_precondition,
        normalized_plan_digest=authorization.authorization.normalized_plan_digest,
    )


def _run(fixture, request):
    from aota_forge.core.ingress import MutationIngressRequest, execute_mutation

    return execute_mutation(
        MutationIngressRequest(
            operation=request.intent.operation,
            store=fixture.store,
            request=request,
        )
    )


def _behavior_checks() -> None:
    from aota_forge.core.catalog import PLAN_INIT_DESCRIPTOR, PLAN_RETIREMENT_DESCRIPTOR
    from aota_forge.core.contracts.mutation import MutationEffect
    from aota_forge.core.ingress import MutationIngressRequest, execute_mutation
    from aota_forge.core.bootstrap import ensure_handlers_bound
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.transitions import capture_retirement_snapshot
    from test_m4_2_m4_4_integration import (
        _issue_authorization,
        _lifecycle_fixture,
        _make_intent,
        _make_plan,
        _plan_init_request,
        _retirement_request,
    )

    ensure_handlers_bound()
    check(
        "M4_3_REGISTRY_CLOSED",
        DEFAULT_REGISTRY.names()
        == (
            "git.inspect",
            "host.status",
            "operations.list",
            "plan_init",
            "plan_retirement",
            "project.resolve",
            "runtime.status",
        ),
    )
    check(
        "M4_3_MUTATION_HANDLERS_BOUND",
        callable(DEFAULT_REGISTRY.handler("plan_init"))
        and callable(DEFAULT_REGISTRY.handler("plan_retirement")),
    )
    check(
        "M4_3_CONTRACT_HASHES_FROZEN",
        PLAN_INIT_DESCRIPTOR.contract_hash()
        == "4c23e2ca954773f33f5f9fc4ef119e8bfba8108c3ca5424a11337d707aade981"
        and PLAN_RETIREMENT_DESCRIPTOR.contract_hash()
        == "ff8c7a76237c5fe15631fd73b6900a292278ccac9c5cac23af82a5b88538252a",
    )

    with _lifecycle_fixture("m43-guard-init") as fixture:
        authorization = _issue_authorization(fixture)
        request = _bound_request(
            _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
            authorization,
        )
        issue_called = False
        from aota_forge.core.authorization import CapabilityLeaseIssuer

        original_issue = CapabilityLeaseIssuer.issue

        def forbidden_issue(*args, **kwargs):
            nonlocal issue_called
            issue_called = True
            raise AssertionError("M4-3 must not issue a lease")

        CapabilityLeaseIssuer.issue = forbidden_issue
        try:
            result = _run(fixture, request)
        finally:
            CapabilityLeaseIssuer.issue = original_issue
        check(
            "T1_VALID_RECEIVED_LEASE_DISPATCHES",
            result["ok"]
            and result["lifecycle_code"] == "PLAN_INIT_APPLIED"
            and result["mutation_effect"] == MutationEffect.APPLIED_VERIFIED.value
            and fixture.store.current_revision(fixture.plan_ref).revision_number == 2,
        )
        check("T1_NO_LEASE_ISSUE", not issue_called)
        check(
            "T1_CANONICAL_AUDIT",
            result["audit"]["validation"] == "ok"
            and result["audit"]["context"] == "ok"
            and result["audit"]["handler"] == "lifecycle",
        )

    with _lifecycle_fixture("m43-guard-missing-lease") as fixture:
        authorization = _issue_authorization(fixture)
        request = _bound_request(
            _plan_init_request(fixture, intent=authorization.intent, lease=None),
            authorization,
        )
        before = fixture.store.read_subject(fixture.plan_ref)
        result = _run(fixture, request)
        check(
            "T2_MISSING_LEASE_FAILS_CLOSED",
            not result["ok"]
            and result["error"]["code"] == "AUTHORIZATION_MISSING"
            and result["audit"]["handler"] == "not_executed"
            and fixture.store.read_subject(fixture.plan_ref) == before,
        )

    with _lifecycle_fixture("m43-guard-retirement", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "m43-guard-retirement",
            retirement_kind="abandoned",
        )
        authorization = _issue_authorization(
            fixture,
            descriptor=PLAN_RETIREMENT_DESCRIPTOR,
            intent=intent,
        )
        result = _run(
            fixture,
            _bound_request(
                _retirement_request(
                    fixture,
                    snapshot,
                    intent=intent,
                    lease=authorization.lease,
                ),
                authorization,
            ),
        )
        check(
            "T3_RETIREMENT_DISPATCHES_EXISTING_LIFECYCLE",
            result["ok"]
            and result["lifecycle_code"] == "RETIREMENT_APPLIED"
            and result["data"]["resulting_plan_state"] == "cancelled",
        )

    with _lifecycle_fixture("m43-guard-binding") as fixture:
        authorization = _issue_authorization(fixture)
        target = _make_plan(fixture.store, "m43-guard-other-target")
        from dataclasses import replace

        request = _bound_request(
            _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
            authorization,
        )
        wrong_target_intent = _make_intent(
            "plan_init",
            target,
            "m43-guard-wrong-target",
            project_id="p1",
            requested_state="initialized",
        )
        result = _run(fixture, replace(request, plan_ref=target, intent=wrong_target_intent))
        check(
            "T4_ACTUAL_TARGET_BOUND_TO_LEASE",
            not result["ok"] and result["error"]["code"] == "LEASE_TARGET_MISMATCH",
        )

    with _lifecycle_fixture("m43-guard-auth-drift") as fixture:
        from dataclasses import replace
        from aota_forge.core.regression.fixtures import fixture_time

        NOW = fixture_time()
        authorization = _issue_authorization(fixture)
        request = _bound_request(
            _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
            authorization,
        )
        first = _run(fixture, request)
        before_revision = fixture.store.current_revision(fixture.plan_ref).revision_number
        changed_authorization = replace(
            authorization.authorization,
            external_authority_precondition="changed-authorization-binding",
        )
        changed_lease = authorization.issuer.issue(
            changed_authorization,
            PLAN_INIT_DESCRIPTOR,
            intent=authorization.intent,
            trusted_context=fixture.context,
            now=NOW,
            lease_id="m43-guard-auth-drift-lease",
            attempt_id="m43-guard-auth-drift-attempt",
        )
        second_request = replace(
            request,
            lease=changed_lease,
            external_authority_precondition=changed_authorization.external_authority_precondition,
        )
        second = _run(fixture, second_request)
        after_revision = fixture.store.current_revision(fixture.plan_ref).revision_number
        check(
            "T5_SAME_KEY_CHANGED_AUTHORIZATION_CONFLICT",
            first["ok"]
            and first["lifecycle_code"] == "PLAN_INIT_APPLIED"
            and first["mutation_effect"] == MutationEffect.APPLIED_VERIFIED.value
            and not second["ok"]
            and second["lifecycle_code"] == "CONFLICT"
            and second["mutation_effect"] == MutationEffect.CONFLICT.value
            and before_revision == 2
            and after_revision == 2,
            f"first={first.get('lifecycle_code')} second={second.get('lifecycle_code')} rev={after_revision}",
        )
        check(
            "T5_NO_SECOND_EFFECT_ON_CONFLICT",
            after_revision == 2,
            f"revision={after_revision}",
        )

    with _lifecycle_fixture("m43-guard-replay") as fixture:
        authorization = _issue_authorization(fixture)
        request = _bound_request(
            _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
            authorization,
        )
        first = _run(fixture, request)
        second = _run(fixture, request)
        check(
            "T6_SAME_KEY_SAME_SEMANTICS_REPLAY",
            first["lifecycle_code"] == "PLAN_INIT_APPLIED"
            and second["lifecycle_code"] == "PLAN_INIT_REPLAYED"
            and second["mutation_effect"] == MutationEffect.REPLAYED_VERIFIED.value
            and fixture.store.current_revision(fixture.plan_ref).revision_number == 2,
        )

    with _lifecycle_fixture("m43-guard-retirement-replay", state="initialized") as fixture:
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "m43-guard-retirement-replay",
            retirement_kind="abandoned",
        )
        authorization = _issue_authorization(
            fixture,
            descriptor=PLAN_RETIREMENT_DESCRIPTOR,
            intent=intent,
        )
        req = _bound_request(
            _retirement_request(
                fixture,
                snapshot,
                intent=intent,
                lease=authorization.lease,
            ),
            authorization,
        )
        first = _run(fixture, req)
        second = _run(fixture, req)
        check(
            "T7_RETIREMENT_SAME_KEY_SAME_SEMANTICS_REPLAY",
            first["lifecycle_code"] == "RETIREMENT_APPLIED"
            and second["lifecycle_code"] == "RETIREMENT_REPLAYED"
            and second["mutation_effect"] == MutationEffect.REPLAYED_VERIFIED.value,
        )

    with _lifecycle_fixture("m43-guard-retirement-auth-drift", state="initialized") as fixture:
        from dataclasses import replace
        from aota_forge.core.regression.fixtures import fixture_time

        NOW = fixture_time()
        snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
        intent = _make_intent(
            "plan_retirement",
            fixture.plan_ref,
            "m43-guard-retirement-auth-drift",
            retirement_kind="abandoned",
        )
        authorization = _issue_authorization(
            fixture,
            descriptor=PLAN_RETIREMENT_DESCRIPTOR,
            intent=intent,
        )
        req = _bound_request(
            _retirement_request(
                fixture,
                snapshot,
                intent=intent,
                lease=authorization.lease,
            ),
            authorization,
        )
        first = _run(fixture, req)
        changed_authorization = replace(
            authorization.authorization,
            external_authority_precondition="changed-retirement-auth",
        )
        changed_lease = authorization.issuer.issue(
            changed_authorization,
            PLAN_RETIREMENT_DESCRIPTOR,
            intent=intent,
            trusted_context=fixture.context,
            now=NOW,
            lease_id="m43-guard-retirement-drift-lease",
            attempt_id="m43-guard-retirement-drift-attempt",
        )
        second_req = replace(
            req,
            lease=changed_lease,
            external_authority_precondition=changed_authorization.external_authority_precondition,
        )
        second = _run(fixture, second_req)
        check(
            "T8_RETIREMENT_SAME_KEY_CHANGED_AUTHORIZATION_CONFLICT",
            first["lifecycle_code"] == "RETIREMENT_APPLIED"
            and second["lifecycle_code"] == "CONFLICT"
            and second["mutation_effect"] == MutationEffect.CONFLICT.value,
            f"first={first.get('lifecycle_code')} second={second.get('lifecycle_code')}",
        )

    check(
        "M4_3_INVALID_ENVELOPE_FAILS_CLOSED",
        execute_mutation({})["error"]["code"] == "INPUT_TYPE_INVALID",
    )


def main() -> int:
    head = _git("rev-parse", "HEAD").strip()
    base_exists = (
        subprocess.run(
            ["git", "-C", str(ROOT), "cat-file", "-e", f"{SOURCE_BASE}^{{commit}}"],
            check=False,
        ).returncode
        == 0
    )
    ancestry = subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", SOURCE_BASE, "HEAD"],
        check=False,
    ).returncode == 0
    check("COMMON_BASE_ANCESTRY_VERIFIED", base_exists and ancestry, f"base={SOURCE_BASE} head={head}")
    check("DIFF_CHECK", subprocess.run(["git", "-C", str(ROOT), "diff", "--check"], check=False).returncode == 0)
    _partition_check()
    _source_boundary_check()
    _behavior_checks()
    passed = all(result[1] for result in RESULTS)
    if passed:
        print(f"M4_3_FOCUSED_TEST_COUNT={len(RESULTS)}")
        print("M4_3_SOURCE_GUARD=PASS")
        return 0
    print("M4_3_SOURCE_GUARD=FAIL")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        print(f"M4_3_SOURCE_GUARD=FAIL: {exc}")
        raise SystemExit(1)
