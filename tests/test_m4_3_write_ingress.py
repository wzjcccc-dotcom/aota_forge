from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

from aota_forge.core.contracts.descriptor import PLAN_RETIREMENT_DESCRIPTOR
from aota_forge.core.contracts.mutation import MutationEffect
from aota_forge.core.ingress import MutationIngressRequest, execute_mutation
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.revision import set_revision_number
from aota_forge.core.transitions import capture_retirement_snapshot

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_m4_2_m4_4_integration import (
    _issue_authorization,
    _lifecycle_fixture,
    _make_intent,
    _make_plan,
    _plan_init_request,
    _retirement_request,
)


def _bound_request(request, authorization):
    return replace(
        request,
        external_authority_precondition=authorization.authorization.external_authority_precondition,
        normalized_plan_digest=authorization.authorization.normalized_plan_digest,
    )


def _run(fixture, request):
    return execute_mutation(
        MutationIngressRequest(
            operation=request.intent.operation,
            store=fixture.store,
            request=request,
        )
    )


class M43WriteIngressTests(unittest.TestCase):
    def test_registry_is_closed_and_handlers_are_bound_lazily(self):
        from aota_forge.core.bootstrap import ensure_handlers_bound
        from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

        ensure_handlers_bound()
        self.assertEqual(
            DEFAULT_REGISTRY.names(),
            (
                "git.inspect",
                "host.status",
                "operations.list",
                "plan_init",
                "plan_retirement",
                "project.resolve",
                "runtime.status",
            ),
        )
        self.assertTrue(callable(DEFAULT_REGISTRY.handler("plan_init")))
        self.assertTrue(callable(DEFAULT_REGISTRY.handler("plan_retirement")))

    def test_plan_init_positive_uses_genuine_lease(self):
        with _lifecycle_fixture("m43-init-positive") as fixture:
            authorization = _issue_authorization(fixture)
            request = _bound_request(
                _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
                authorization,
            )

            result = _run(fixture, request)

            self.assertTrue(result["ok"])
            self.assertEqual(result["lifecycle_code"], "PLAN_INIT_APPLIED")
            self.assertEqual(result["mutation_effect"], MutationEffect.APPLIED_VERIFIED.value)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "initialized")
            self.assertEqual(fixture.store.current_revision(fixture.plan_ref).revision_number, 2)

    def test_plan_init_reentry_is_lifecycle_denied_after_lease_validation(self):
        with _lifecycle_fixture("m43-init-reentry", state="initialized") as fixture:
            authorization = _issue_authorization(fixture)
            before = fixture.store.read_subject(fixture.plan_ref)
            result = _run(
                fixture,
                _bound_request(
                    _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
                    authorization,
                ),
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["lifecycle_code"], "PLAN_INIT_ALREADY_INITIALIZED")
            self.assertEqual(result["mutation_effect"], MutationEffect.NO_EFFECT.value)
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref), before)

    def test_ambiguous_project_remains_semantic_choice(self):
        with _lifecycle_fixture("m43-init-ambiguous") as fixture:
            duplicate_parent = fixture.workspace.workdir / "duplicate"
            duplicate_parent.mkdir()
            fixture.workspace.create_project("p1", parent_dir=duplicate_parent)
            evidence = resolve_project_candidates("w1", fixture.registry, "p1")
            authorization = _issue_authorization(fixture)
            request = _bound_request(
                replace(
                    _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
                    project_evidence=evidence,
                    project_binding=None,
                ),
                authorization,
            )

            result = _run(fixture, request)

            self.assertEqual(evidence.status, "NEEDS_SEMANTIC_CHOICE")
            self.assertTrue(result["ok"])
            self.assertEqual(result["lifecycle_code"], "NEEDS_SEMANTIC_CHOICE")
            self.assertEqual(result["mutation_effect"], MutationEffect.NEEDS_SEMANTIC_CHOICE.value)
            self.assertEqual(fixture.store.current_revision(fixture.plan_ref).revision_number, 1)

    def test_plan_init_idempotency_replay_and_changed_intent_conflict(self):
        with _lifecycle_fixture("m43-init-idempotency") as fixture:
            authorization = _issue_authorization(fixture)
            request = _bound_request(
                _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
                authorization,
            )
            first = _run(fixture, request)
            replay = _run(fixture, request)
            changed_intent = _make_intent(
                "plan_init",
                fixture.plan_ref,
                authorization.intent.idempotency_key,
                project_id="other",
                requested_state="initialized",
            )
            changed_authorization = _issue_authorization(fixture, intent=changed_intent)
            conflict = _run(
                fixture,
                _bound_request(
                    replace(request, intent=changed_intent, lease=changed_authorization.lease),
                    changed_authorization,
                ),
            )

            self.assertEqual(first["lifecycle_code"], "PLAN_INIT_APPLIED")
            self.assertEqual(replay["lifecycle_code"], "PLAN_INIT_REPLAYED")
            self.assertEqual(replay["mutation_effect"], MutationEffect.REPLAYED_VERIFIED.value)
            self.assertEqual(conflict["lifecycle_code"], "CONFLICT")
            self.assertEqual(conflict["mutation_effect"], MutationEffect.CONFLICT.value)
            self.assertEqual(fixture.store.current_revision(fixture.plan_ref).revision_number, 2)

    def test_wrong_intent_contract_target_and_authority_bindings_fail_before_mutation(self):
        cases = ("intent", "contract", "external", "source", "observed", "candidate", "normalized", "target", "scope")
        for case in cases:
            with self.subTest(case=case), _lifecycle_fixture(f"m43-binding-{case}") as fixture:
                authorization = _issue_authorization(fixture)
                request = _bound_request(
                    _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
                    authorization,
                )
                expected_code = ""
                if case == "intent":
                    request = replace(
                        request,
                        intent=_make_intent("plan_init", fixture.plan_ref, "changed-intent", project_id="p1", requested_state="other"),
                    )
                    expected_code = "LEASE_INTENT_MISMATCH"
                elif case == "contract":
                    request = replace(request, lease=replace(authorization.lease, contract_hash="f" * 64))
                    expected_code = "AUTHORIZATION_CONTRACT_DRIFT"
                elif case == "external":
                    request = replace(request, external_authority_precondition="source-revision-8")
                    expected_code = "AUTHORITY_PRECONDITION_STALE"
                elif case == "source":
                    request = replace(request, preconditions=replace(request.preconditions, authority_source_revision="8"))
                    expected_code = "AUTHORITY_PRECONDITION_STALE"
                elif case == "observed":
                    request = replace(request, preconditions=replace(request.preconditions, authority_observed_raw_digest="d" * 64))
                    expected_code = "AUTHORITY_PRECONDITION_STALE"
                elif case == "candidate":
                    request = replace(request, preconditions=replace(request.preconditions, candidate_raw_digest="e" * 64))
                    expected_code = "AUTHORITY_PRECONDITION_STALE"
                elif case == "normalized":
                    request = replace(request, normalized_plan_digest="d" * 64)
                    expected_code = "AUTHORITY_PRECONDITION_STALE"
                elif case == "target":
                    target = _make_plan(fixture.store, f"m43-binding-target-{case}")
                    intent = _make_intent("plan_init", target, "changed-target", project_id="p1", requested_state="initialized")
                    request = replace(request, plan_ref=target, intent=intent)
                    expected_code = "LEASE_TARGET_MISMATCH"
                elif case == "scope":
                    intent = _make_intent(
                        "plan_init",
                        fixture.plan_ref,
                        "changed-scope",
                        project_id="p1",
                        requested_state="initialized",
                    )
                    intent = replace(intent, mutation_scope={"mode": "different"})
                    request = replace(request, intent=intent)
                    expected_code = "LEASE_SCOPE_MISMATCH"

                before = fixture.store.read_subject(fixture.plan_ref)
                result = _run(fixture, request)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], expected_code)
                self.assertEqual(fixture.store.read_subject(fixture.plan_ref), before)

    def test_retirement_positive_abandoned_and_exact_successor(self):
        with _lifecycle_fixture("m43-retirement-abandoned", state="initialized") as fixture:
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent("plan_retirement", fixture.plan_ref, "m43-abandoned", retirement_kind="abandoned")
            authorization = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = _run(
                fixture,
                _bound_request(
                    _retirement_request(fixture, snapshot, intent=intent, lease=authorization.lease),
                    authorization,
                ),
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["lifecycle_code"], "RETIREMENT_APPLIED")
            self.assertEqual(result["data"]["resulting_plan_state"], "cancelled")

        with _lifecycle_fixture("m43-retirement-successor", state="initialized") as fixture:
            successor = _make_plan(fixture.store, "m43-successor", state="initialized")
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent(
                "plan_retirement",
                fixture.plan_ref,
                "m43-superseded",
                retirement_kind="superseded",
                successor_ref=successor.serialize(),
            )
            authorization = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = _run(
                fixture,
                _bound_request(
                    _retirement_request(fixture, snapshot, intent=intent, lease=authorization.lease, successor=successor),
                    authorization,
                ),
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["lifecycle_code"], "RETIREMENT_APPLIED")
            self.assertEqual(result["data"]["resulting_plan_state"], "superseded")

    def test_retirement_requires_exact_successor_and_rejects_drift(self):
        with _lifecycle_fixture("m43-retirement-missing", state="initialized") as fixture:
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            intent = _make_intent("plan_retirement", fixture.plan_ref, "m43-missing", retirement_kind="superseded")
            authorization = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = _run(
                fixture,
                _bound_request(
                    replace(
                        _retirement_request(fixture, snapshot, intent=intent, lease=authorization.lease),
                        retirement_kind="superseded",
                    ),
                    authorization,
                ),
            )
            self.assertEqual(result["lifecycle_code"], "RETIREMENT_SUCCESSOR_REQUIRED")
            self.assertEqual(fixture.store.current_revision(fixture.plan_ref).revision_number, 1)

        with _lifecycle_fixture("m43-retirement-drift", state="initialized") as fixture:
            successor = _make_plan(fixture.store, "m43-drift-successor", state="initialized")
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            successor_subject = fixture.store.read_subject(successor)
            fixture.store._put_staged(set_revision_number(successor_subject, 2))
            intent = _make_intent(
                "plan_retirement",
                fixture.plan_ref,
                "m43-drift",
                retirement_kind="superseded",
                successor_ref=successor.serialize(),
            )
            authorization = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            result = _run(
                fixture,
                _bound_request(
                    _retirement_request(fixture, snapshot, intent=intent, lease=authorization.lease, successor=successor),
                    authorization,
                ),
            )
            self.assertEqual(result["lifecycle_code"], "RETIREMENT_STALE_SNAPSHOT")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "initialized")

    def test_valid_lease_does_not_bypass_retirement_protection_or_resurrection(self):
        for name, flags, expected in (
            ("active", {"active": True, "current": True}, "RETIREMENT_TARGET_PROTECTED"),
            ("running", {"running_task": True}, "RETIREMENT_RUNNING_TASK_PROTECTED"),
        ):
            with self.subTest(name=name), _lifecycle_fixture(f"m43-protected-{name}", state="initialized") as fixture:
                subject = fixture.store.read_subject(fixture.plan_ref)
                fixture.store._put_staged(replace(subject, mechanical_state={**subject.mechanical_state, **flags}))
                snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
                intent = _make_intent("plan_retirement", fixture.plan_ref, f"m43-{name}", retirement_kind="abandoned")
                authorization = _issue_authorization(fixture, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
                result = _run(
                    fixture,
                    _bound_request(
                        _retirement_request(fixture, snapshot, intent=intent, lease=authorization.lease),
                        authorization,
                    ),
                )
                self.assertEqual(result["lifecycle_code"], expected)

        with _lifecycle_fixture("m43-resurrection", state="cancelled") as fixture:
            authorization = _issue_authorization(fixture)
            request = _bound_request(
                _plan_init_request(fixture, intent=authorization.intent, lease=authorization.lease),
                authorization,
            )
            result = _run(fixture, request)
            self.assertEqual(result["lifecycle_code"], "PLAN_INIT_INVALID_PREDECESSOR")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "cancelled")

    def test_received_lease_only_and_invalid_envelopes_fail_closed(self):
        with _lifecycle_fixture("m43-envelope") as fixture:
            self.assertEqual(execute_mutation({})["error"]["code"], "INPUT_TYPE_INVALID")
            unknown = MutationIngressRequest("unknown_operation", fixture.store, None)  # type: ignore[arg-type]
            self.assertEqual(execute_mutation(unknown)["error"]["code"], "UNSUPPORTED_OPERATION")
            read_request = MutationIngressRequest("operations.list", fixture.store, None)  # type: ignore[arg-type]
            self.assertEqual(execute_mutation(read_request)["error"]["code"], "UNSUPPORTED_OPERATION")

            authorization = _issue_authorization(fixture)
            request = _bound_request(
                _plan_init_request(fixture, intent=authorization.intent, lease=None),
                authorization,
            )
            missing_lease = _run(fixture, request)
            self.assertEqual(missing_lease["error"]["code"], "AUTHORIZATION_MISSING")

    def test_wrong_operation_lease_is_rejected_before_retirement(self):
        with _lifecycle_fixture("m43-operation-mismatch", state="initialized") as fixture:
            snapshot = capture_retirement_snapshot(fixture.store, fixture.plan_ref)
            init_authorization = _issue_authorization(fixture)
            intent = _make_intent("plan_retirement", fixture.plan_ref, "m43-operation-mismatch", retirement_kind="abandoned")
            request = _bound_request(
                _retirement_request(fixture, snapshot, intent=intent, lease=init_authorization.lease),
                init_authorization,
            )
            result = _run(fixture, request)
            self.assertEqual(result["error"]["code"], "AUTHORIZATION_OPERATION_MISMATCH")
            self.assertEqual(fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"], "initialized")


if __name__ == "__main__":
    unittest.main()
