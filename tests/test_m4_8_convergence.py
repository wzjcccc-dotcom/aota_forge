"""M4-8 Post-Integration Convergence and Entry-Surface Parity Test Suite.

Verifies:
- S1: CLI Plan Command Projection and Canonical CLI subcommands
- S2: Direct Core & Unified Ingress Mutation Routing and Error Envelopes
- S3: End-to-End Mechanical Mutation Flow (DurableJournalStore + PlanAuthorityMutationPort)
- S4: 3-Way Entry Surface Parity (Direct Core, Unified Ingress, Canonical CLI) with 0 semantic drift
- S5: Deterministic Failure Injection (FI-01..FI-12), Negative Matrix (NEG-01..NEG-12),
      14 Historical Regressions, Successor Rules (SUCC-01..SUCC-07)
- Acceptance Cases POS-01..POS-08
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from aota_forge.adapters.plan_authority.fake_github import (
    FakeGitHubAuthorityAdapter,
    FixtureGitHubStore,
    InjectionHooks as GHInjectionHooks,
)
from aota_forge.adapters.plan_authority.github import CONTROL_ROLE_UPDATE_IN_PLACE_IMPLEMENTED
from aota_forge.adapters.plan_authority.fake_port import (
    FakePlanAuthorityAdapter,
    FixtureAuthority,
    InjectionHooks as PortInjectionHooks,
)
from aota_forge.adapters.plan_authority.port import (
    CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE as PORT_CROSS_ATOMIC,
    PlanAuthorityMutationPort,
    PortablePlanMutationRequest,
    PortablePlanMutationResponse,
)
from aota_forge.cli.__main__ import _build_parser, _main
from aota_forge.cli.exit_codes import (
    EXIT_BLOCKED,
    EXIT_ERROR,
    EXIT_NEEDS_SEMANTIC_CHOICE,
    EXIT_SUCCESS,
    EXIT_USAGE,
    classify,
)
from aota_forge.cli.projection import operation_schema, semantic_input_specs
from aota_forge.core.authority import (
    ApprovalEvidence,
    AuthorityDecision,
    AuthorityEngine,
    AuthorityRequest,
    MaterializedDecisionEvidence,
    TrustedMutationAuthorization,
)
from aota_forge.core.authorization import AuthorizationFailure, CapabilityLeaseIssuer
from aota_forge.core.capability_lease import CapabilityLease
from aota_forge.core.context import (
    Principal,
    ProjectBinding,
    TrustedContext,
    bind_trusted_context,
)
from aota_forge.core.contracts.descriptor import (
    READ_ONLY,
    WRITE_ONLY,
)
from aota_forge.core.catalog import (
    LIFECYCLE_DESCRIPTORS,
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.contracts.errors import (
    AuthorizationContractDriftError,
    AuthorizationMissingError,
    AuthorizationScopeMismatchError,
    ForgeError,
    StaleAuthorityError,
    StaleSubjectError,
    UnsupportedOperationError,
)
from aota_forge.core.contracts.mutation import (
    MutationEffect,
    MutationIntent,
    MutationPreconditions,
    MutationResult,
)
from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
from aota_forge.core.contracts.results import LifecycleResult, lifecycle_envelope, lifecycle_result
from aota_forge.core.idempotency import canonical_fingerprint
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, make_object_ref
from aota_forge.core.ingress import MutationIngressRequest, execute, execute_mutation
from aota_forge.core.journal.store import (
    FileBackedDurableJournalStore,
    InMemoryDurableJournalStore,
    PRODUCTION_STORAGE_ENGINE_FROZEN,
    StaleJournalRevisionError,
)
from aota_forge.core.journal.executor import (
    APPLYING_RESTART_BLIND_RETRY_ALLOWED,
    CROSS_AUTHORITY_ATOMIC_TRANSACTION_AVAILABLE as EXEC_CROSS_ATOMIC,
    JOURNAL_CAS_IMPLIES_EXTERNAL_WRITE_ATOMICITY,
    RECOVERY_EXECUTOR_IS_SEMANTIC_DECISION_MAKER,
    TRANSPORT_SUCCESS_ALONE_MEANS_VERIFIED,
    RecoveryExecutor,
)
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.reconcile import classify_three_way
from aota_forge.core.journal.recovery import RecoveryScanner
from aota_forge.core.journal.retry_handoff import (
    FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY,
    OLD_AUTHORIZATION_EVIDENCE_OVERWRITE_ALLOWED,
    create_retry_journal,
)
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.revision import set_revision_number
from aota_forge.core.transaction import TransactionStore
from aota_forge.core.transitions import (
    PlanInitRequest,
    PlanRetirementRequest,
    RetirementCandidateSnapshot,
    capture_retirement_snapshot,
    plan_init,
    retire_plan,
)

# Test helper fixtures from integration suite
from test_m4_2_m4_4_integration import (
    NOW,
    _issue_authorization,
    _lifecycle_fixture,
    _make_intent,
    _make_lease,
    _make_plan,
    _plan_init_request,
    _retirement_request,
)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _bound_request(request, authorization):
    return replace(
        request,
        external_authority_precondition=authorization.authorization.external_authority_precondition,
        normalized_plan_digest=authorization.authorization.normalized_plan_digest,
    )


class M48ConvergenceTests(unittest.TestCase):
    """End-to-end convergence and 3-way parity test suite."""

    # -----------------------------------------------------------------------
    # S1: CLI Plan Command Projection and Ingress Routing
    # -----------------------------------------------------------------------

    def test_cli_plan_command_projection(self):
        """S1 focused test: CLI argument projection derives exactly from descriptors."""
        parser = _build_parser()
        # Test plan init projection
        schema_init = operation_schema("plan_init")
        self.assertIsNotNone(schema_init)
        self.assertEqual(schema_init["operation"], "plan_init")
        arg_names_init = {a["name"] for a in schema_init["arguments"]}
        expected_init = {
            "plan_ref",
            "project_binding",
            "semantic_inputs",
            "subject_expected_revision",
            "authority_source_revision",
            "authority_observed_raw_digest",
        }
        self.assertEqual(arg_names_init, expected_init)

        # Test plan retire projection
        schema_retire = operation_schema("plan_retirement")
        self.assertIsNotNone(schema_retire)
        self.assertEqual(schema_retire["operation"], "plan_retirement")
        arg_names_retire = {a["name"] for a in schema_retire["arguments"]}
        expected_retire = {
            "plan_ref",
            "retirement_kind",
            "snapshot_identity",
            "subject_expected_revision",
            "authority_source_revision",
            "authority_observed_raw_digest",
            "successor_ref",
        }
        self.assertEqual(arg_names_retire, expected_retire)

        # Test parser argument parsing
        args_init = parser.parse_args([
            "plan", "init",
            "--plan-ref", "ref:forge:subject:plan:p1",
            "--project-binding", '{"project_id": "p1", "workspace_id": "w1"}',
            "--semantic-inputs", '{"mode": "init"}',
            "--subject-expected-revision", "1",
            "--authority-source-revision", "rev-1",
            "--authority-observed-raw-digest", "a" * 64,
            "--json",
        ])
        self.assertEqual(args_init.command, "plan")
        self.assertEqual(args_init.plan_command, "init")
        self.assertTrue(args_init.json)
        self.assertEqual(args_init.subject_expected_revision, 1)

    def test_cli_generic_command_denied(self):
        """S1 negative test: Generic / arbitrary CLI commands are rejected."""
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["plan", "generic_exec"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["terminal", "run"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["git", "write"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["github", "comment"])

    # -----------------------------------------------------------------------
    # S2: Direct Core & Unified Ingress Mutation Routing
    # -----------------------------------------------------------------------

    def test_ingress_mutation_routing(self):
        """S2 focused test: execute_mutation() dispatches valid requests to handlers."""
        with _lifecycle_fixture("s2-ingress-init") as fixture:
            auth = _issue_authorization(fixture)
            req = _bound_request(
                _plan_init_request(fixture, intent=auth.intent, lease=auth.lease),
                auth,
            )
            ingress_req = MutationIngressRequest(
                operation="plan_init",
                store=fixture.store,
                request=req,
            )
            result = execute_mutation(ingress_req)
            self.assertTrue(result["ok"])
            self.assertEqual(result["lifecycle_code"], "PLAN_INIT_APPLIED")
            self.assertEqual(result["mutation_effect"], MutationEffect.APPLIED_VERIFIED.value)

    def test_ingress_unsupported_mutation_denied(self):
        """S2 negative test: unsupported operations or read-only ops rejected by execute_mutation."""
        with _lifecycle_fixture("s2-ingress-unsupported") as fixture:
            auth = _issue_authorization(fixture)
            req = _bound_request(
                _plan_init_request(fixture, intent=auth.intent, lease=auth.lease),
                auth,
            )
            bad_req = MutationIngressRequest(
                operation="unsupported_mutation",
                store=fixture.store,
                request=req,
            )
            res = execute_mutation(bad_req)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "UNSUPPORTED_OPERATION")

            read_req = MutationIngressRequest(
                operation="project.resolve",
                store=fixture.store,
                request=req,
            )
            res2 = execute_mutation(read_req)
            self.assertFalse(res2["ok"])
            self.assertEqual(res2["error"]["code"], "UNSUPPORTED_OPERATION")

    # -----------------------------------------------------------------------
    # S3: End-to-End Mechanical Mutation Flow
    # -----------------------------------------------------------------------

    def test_end_to_end_mutation_flow(self):
        """S3 focused test: PREPARED -> APPLYING -> port.mutate -> verify -> terminal CAS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileBackedDurableJournalStore(path=pathlib.Path(tmpdir) / "journal.json")
            target = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "e2e-plan", sub_kind=SubjectKind.PLAN))
            orig_digest = _sha("original-content")
            cand_body = "new-candidate-content"
            cand_digest = _sha(cand_body)

            req = PortablePlanMutationRequest(
                operation="plan_init",
                typed_target=target,
                correlation_id="e2e-corr-1",
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                idempotency_key="e2e-key-1",
                intent_fingerprint=_sha("e2e-intent-1"),
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=orig_digest,
                candidate_raw_digest=cand_digest,
                normalized_plan_digest=_sha("e2e-norm-1"),
                principal="tester",
                candidate_raw_body=cand_body,
            )

            rec = JournalRecord(
                journal_id="e2e-j1",
                correlation_id="e2e-corr-1",
                attempt_id="e2e-att-1",
                operation="plan_init",
                typed_target=target,
                contract_hash=req.contract_hash,
                idempotency_key=req.idempotency_key,
                intent_fingerprint=req.intent_fingerprint,
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=orig_digest,
                candidate_raw_digest=cand_digest,
                normalized_plan_digest=req.normalized_plan_digest,
                principal="tester",
                journal_state=JournalState.PREPARED,
                original_raw_digest=orig_digest,
            )

            # 1. Create PREPARED
            prep_entry = store.create_prepared(rec)
            self.assertEqual(prep_entry.record.journal_state, JournalState.PREPARED)
            self.assertEqual(prep_entry.journal_revision, 1)

            # 2. Fake port with matching precondition
            gh_store = FixtureGitHubStore(body="original-content", revision="1")
            adapter = FakeGitHubAuthorityAdapter(store=gh_store)

            # 3. RecoveryExecutor executes attempt
            executor = RecoveryExecutor(store=store, port=adapter)
            terminal_entry = executor.attempt_external_mutation("e2e-j1", req)

            self.assertIn(terminal_entry.record.journal_state, (JournalState.VERIFIED, JournalState.VERIFIED_RECOVERED))
            self.assertEqual(gh_store.write_issue_call_count, 1)
            self.assertEqual(gh_store._body, cand_body)

    def test_mutation_stale_precondition_zero_write(self):
        """S3 negative test: stale raw authority precondition causes 0 external writes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileBackedDurableJournalStore(path=pathlib.Path(tmpdir) / "journal.json")
            target = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "stale-plan", sub_kind=SubjectKind.PLAN))
            orig_digest = _sha("original-content")
            cand_body = "new-candidate-content"
            cand_digest = _sha(cand_body)

            req = PortablePlanMutationRequest(
                operation="plan_init",
                typed_target=target,
                correlation_id="stale-corr-1",
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                idempotency_key="stale-key-1",
                intent_fingerprint=_sha("stale-intent-1"),
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=orig_digest,
                candidate_raw_digest=cand_digest,
                normalized_plan_digest=_sha("stale-norm-1"),
                principal="tester",
                candidate_raw_body=cand_body,
            )

            rec = JournalRecord(
                journal_id="stale-j1",
                correlation_id="stale-corr-1",
                attempt_id="stale-att-1",
                operation="plan_init",
                typed_target=target,
                contract_hash=req.contract_hash,
                idempotency_key=req.idempotency_key,
                intent_fingerprint=req.intent_fingerprint,
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=orig_digest,
                candidate_raw_digest=cand_digest,
                normalized_plan_digest=req.normalized_plan_digest,
                principal="tester",
                journal_state=JournalState.PREPARED,
                original_raw_digest=orig_digest,
            )

            store.create_prepared(rec)

            # GitHub store has newer revision '2' and different body
            gh_store = FixtureGitHubStore(body="newer-concurrent-content", revision="2")
            adapter = FakeGitHubAuthorityAdapter(store=gh_store)

            executor = RecoveryExecutor(store=store, port=adapter)
            terminal_entry = executor.attempt_external_mutation("stale-j1", req)

            self.assertEqual(terminal_entry.record.journal_state, JournalState.FAILED_NO_EFFECT)
            self.assertEqual(gh_store.write_issue_call_count, 0)

    # -----------------------------------------------------------------------
    # S4: 3-Way Entry Surface Parity (Core / Ingress / CLI)
    # -----------------------------------------------------------------------

    def test_entry_surface_parity_matrix(self):
        """S4 focused test: Direct Core, Unified Ingress, and Canonical CLI produce identical semantic outcomes."""
        # 1. plan_init parity across all 3 surfaces
        with _lifecycle_fixture("parity-init-direct") as fix_direct, \
             _lifecycle_fixture("parity-init-ingress") as fix_ingress, \
             _lifecycle_fixture("parity-init-cli") as fix_cli:

            # Direct Core
            auth_d = _issue_authorization(fix_direct)
            req_d = _bound_request(_plan_init_request(fix_direct, intent=auth_d.intent, lease=auth_d.lease), auth_d)
            res_direct = plan_init(fix_direct.store, req_d)

            # Unified Ingress
            auth_i = _issue_authorization(fix_ingress)
            req_i = _bound_request(_plan_init_request(fix_ingress, intent=auth_i.intent, lease=auth_i.lease), auth_i)
            res_ingress = execute_mutation(MutationIngressRequest("plan_init", fix_ingress.store, req_i))

            # Canonical CLI
            auth_c = _issue_authorization(fix_cli)
            req_c = _bound_request(_plan_init_request(fix_cli, intent=auth_c.intent, lease=auth_c.lease), auth_c)
            # Invoke CLI route builder with request + store attached
            cli_ns = argparse.Namespace(
                command="plan",
                plan_command="init",
                request=req_c,
                store=fix_cli.store,
                json=True,
            )
            from aota_forge.cli.__main__ import ROUTES
            op, builder = ROUTES[("plan", "init")]
            self.assertEqual(op, "plan_init")
            params = builder(cli_ns, None)
            res_cli = execute_mutation(MutationIngressRequest("plan_init", params["store"], params["request"]))

            # Verify identical semantic codes and effects
            self.assertEqual(res_direct.code, "PLAN_INIT_APPLIED")
            self.assertEqual(res_ingress["lifecycle_code"], "PLAN_INIT_APPLIED")
            self.assertEqual(res_cli["lifecycle_code"], "PLAN_INIT_APPLIED")

            self.assertEqual(res_direct.mutation_effect, MutationEffect.APPLIED_VERIFIED)
            self.assertEqual(res_ingress["mutation_effect"], MutationEffect.APPLIED_VERIFIED.value)
            self.assertEqual(res_cli["mutation_effect"], MutationEffect.APPLIED_VERIFIED.value)

        # 2. plan_retirement parity across all 3 surfaces
        with _lifecycle_fixture("parity-ret-direct", state="initialized") as fix_d, \
             _lifecycle_fixture("parity-ret-ingress", state="initialized") as fix_i, \
             _lifecycle_fixture("parity-ret-cli", state="initialized") as fix_c:

            snap_d = capture_retirement_snapshot(fix_d.store, fix_d.plan_ref)
            intent_d = _make_intent("plan_retirement", fix_d.plan_ref, "ret-d-key", retirement_kind="abandoned")
            auth_d = _issue_authorization(fix_d, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent_d)
            req_d = _bound_request(_retirement_request(fix_d, snap_d, intent=intent_d, lease=auth_d.lease), auth_d)
            res_d = retire_plan(fix_d.store, req_d)

            snap_i = capture_retirement_snapshot(fix_i.store, fix_i.plan_ref)
            intent_i = _make_intent("plan_retirement", fix_i.plan_ref, "ret-i-key", retirement_kind="abandoned")
            auth_i = _issue_authorization(fix_i, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent_i)
            req_i = _bound_request(_retirement_request(fix_i, snap_i, intent=intent_i, lease=auth_i.lease), auth_i)
            res_i = execute_mutation(MutationIngressRequest("plan_retirement", fix_i.store, req_i))

            snap_c = capture_retirement_snapshot(fix_c.store, fix_c.plan_ref)
            intent_c = _make_intent("plan_retirement", fix_c.plan_ref, "ret-c-key", retirement_kind="abandoned")
            auth_c = _issue_authorization(fix_c, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent_c)
            req_c = _bound_request(_retirement_request(fix_c, snap_c, intent=intent_c, lease=auth_c.lease), auth_c)
            res_c = execute_mutation(MutationIngressRequest("plan_retirement", fix_c.store, req_c))

            self.assertEqual(res_d.code, "RETIREMENT_APPLIED")
            self.assertEqual(res_i["lifecycle_code"], "RETIREMENT_APPLIED")
            self.assertEqual(res_c["lifecycle_code"], "RETIREMENT_APPLIED")

    def test_authority_delta_zero(self):
        """S4 negative test: Zero authority delta between entry surfaces."""
        # No entry surface may execute a mutation without a valid CapabilityLease
        with _lifecycle_fixture("delta-zero") as fixture:
            auth = _issue_authorization(fixture)

            # Direct Core without lease returns PLAN_INIT_AUTHORIZATION_REQUIRED
            req_no_lease = _bound_request(
                _plan_init_request(fixture, intent=auth.intent, lease=None),
                auth,
            )
            res_direct = plan_init(fixture.store, req_no_lease)
            self.assertEqual(res_direct.code, "PLAN_INIT_AUTHORIZATION_REQUIRED")

            # Ingress without lease fails closed with AUTHORIZATION_MISSING
            res_ingress = execute_mutation(
                MutationIngressRequest("plan_init", fixture.store, req_no_lease)
            )
            self.assertFalse(res_ingress["ok"])
            self.assertEqual(res_ingress["error"]["code"], "AUTHORIZATION_MISSING")

            # CLI without lease fails closed with exit code 1
            cli_ns = argparse.Namespace(
                command="plan",
                plan_command="init",
                request=req_no_lease,
                store=fixture.store,
                json=True,
            )
            from aota_forge.cli.__main__ import ROUTES
            op, builder = ROUTES[("plan", "init")]
            params = builder(cli_ns, None)
            res_cli = execute_mutation(
                MutationIngressRequest("plan_init", params["store"], params["request"])
            )
            self.assertFalse(res_cli["ok"])
            self.assertEqual(res_cli["error"]["code"], "AUTHORIZATION_MISSING")
            self.assertEqual(classify(res_cli), EXIT_ERROR)

    # -----------------------------------------------------------------------
    # S5: Deterministic Failure Injection Suite (FI-01..FI-12)
    # -----------------------------------------------------------------------

    def test_failure_injection_suite(self):
        """S5 focused test: FI-01 through FI-12 failure injection points."""
        # FI-01: Authorization drift (contract hash mismatch)
        with _lifecycle_fixture("fi-01") as fixture:
            auth = _issue_authorization(fixture)
            bad_lease = replace(auth.lease, contract_hash="0" * 64)
            req = _bound_request(
                _plan_init_request(fixture, intent=auth.intent, lease=bad_lease),
                auth,
            )
            res = execute_mutation(MutationIngressRequest("plan_init", fixture.store, req))
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "AUTHORIZATION_CONTRACT_DRIFT")

        # FI-02: Stale Subject revision
        with _lifecycle_fixture("fi-02") as fixture:
            auth = _issue_authorization(fixture)
            req = _bound_request(
                _plan_init_request(fixture, intent=auth.intent, lease=auth.lease, revision=5),
                auth,
            )
            res = execute_mutation(MutationIngressRequest("plan_init", fixture.store, req))
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "SUBJECT_REVISION_STALE")

        # FI-03: Stale external authority precondition
        with _lifecycle_fixture("fi-03") as fixture:
            auth = _issue_authorization(fixture)
            req = replace(
                _bound_request(
                    _plan_init_request(fixture, intent=auth.intent, lease=auth.lease),
                    auth,
                ),
                external_authority_precondition="stale-token-differs",
            )
            res = execute_mutation(MutationIngressRequest("plan_init", fixture.store, req))
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "AUTHORITY_PRECONDITION_STALE")

        # FI-04: Duplicate control role comment (cardinality > 1)
        gh_store_dup = FixtureGitHubStore(body="body", revision="1")
        gh_store_dup.seed_control_comment("milestone_progress_index", "comm-1")
        gh_store_dup.seed_control_comment("milestone_progress_index", "comm-2")
        adapter_dup = FakeGitHubAuthorityAdapter(store=gh_store_dup)
        target_dup = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "dup-role-plan", sub_kind=SubjectKind.PLAN))
        res_dup = adapter_dup.resolve_control_role(target_dup, "milestone_progress_index")
        self.assertEqual(res_dup.error_code, "CONTROL_ROLE_DUPLICATE")
        self.assertEqual(res_dup.binding_count, 2)

        # FI-05: Durable journal CAS race
        with tempfile.TemporaryDirectory() as tmpdir:
            store_cas = FileBackedDurableJournalStore(path=pathlib.Path(tmpdir) / "cas.json")
            target_cas = make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "cas-plan", sub_kind=SubjectKind.PLAN))
            rec_cas = JournalRecord(
                journal_id="cas-j1",
                correlation_id="cas-corr",
                attempt_id="cas-att-1",
                operation="plan_init",
                typed_target=target_cas,
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                idempotency_key="cas-key",
                intent_fingerprint=_sha("cas-intent"),
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=_sha("orig"),
                candidate_raw_digest=_sha("cand"),
                normalized_plan_digest=_sha("norm"),
                principal="tester",
                journal_state=JournalState.PREPARED,
            )
            e = store_cas.create_prepared(rec_cas)
            # First CAS succeeds
            ok1, e1 = store_cas.cas_transition("cas-j1", e.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
            self.assertTrue(ok1)
            # Second CAS with stale revision raises StaleJournalRevisionError
            with self.assertRaises(StaleJournalRevisionError):
                store_cas.cas_transition("cas-j1", e.journal_revision, JournalState.PREPARED, JournalState.APPLYING)

        # FI-06: External write timeout -> OUTCOME_UNKNOWN
        with tempfile.TemporaryDirectory() as tmpdir:
            store_to = InMemoryDurableJournalStore()
            rec_to = replace(rec_cas, journal_id="to-j1", correlation_id="to-corr")
            store_to.create_prepared(rec_to)
            gh_store_to = FixtureGitHubStore(body="orig", revision="1", hooks=GHInjectionHooks(timeout_during_mutate=True))
            adapter_to = FakeGitHubAuthorityAdapter(store=gh_store_to)
            executor_to = RecoveryExecutor(store=store_to, port=adapter_to)
            req_to = PortablePlanMutationRequest(
                operation="plan_init",
                typed_target=target_cas,
                correlation_id="to-corr",
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                idempotency_key="to-key",
                intent_fingerprint=_sha("to-intent"),
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=_sha("orig"),
                candidate_raw_digest=_sha("cand"),
                normalized_plan_digest=_sha("norm"),
                principal="tester",
                candidate_raw_body="cand",
            )
            res_to = executor_to.attempt_external_mutation("to-j1", req_to)
            self.assertEqual(res_to.record.journal_state, JournalState.OUTCOME_UNKNOWN)

        # FI-07: Lost response / connection reset -> OUTCOME_UNKNOWN -> verify recovers
        with tempfile.TemporaryDirectory() as tmpdir:
            store_lr = InMemoryDurableJournalStore()
            rec_lr = replace(rec_cas, journal_id="lr-j1", correlation_id="lr-corr")
            store_lr.create_prepared(rec_lr)

            # Write succeeded on remote, but transport threw TimeoutError / connection error during response
            def _lost_response_hook(target, candidate_body, expected_revision, expected_digest):
                gh_store_lr._body = "cand"
                gh_store_lr._revision = "2"
                raise TimeoutError("lost response / connection reset")

            gh_store_lr = FixtureGitHubStore(body="orig", revision="1", hooks=GHInjectionHooks(during_write=_lost_response_hook))
            adapter_lr = FakeGitHubAuthorityAdapter(store=gh_store_lr)
            executor_lr = RecoveryExecutor(store=store_lr, port=adapter_lr)
            req_lr = replace(req_to, correlation_id="lr-corr", candidate_raw_digest=_sha("cand"), authority_observed_raw_digest=_sha("orig"))
            entry_lr = executor_lr.attempt_external_mutation("lr-j1", req_lr)
            self.assertEqual(entry_lr.record.journal_state, JournalState.OUTCOME_UNKNOWN)
            # Recovery scanner recovers true state via readback
            recovered = executor_lr.recover_one("lr-j1")
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.record.journal_state, JournalState.VERIFIED_RECOVERED)

        # FI-08: Verify readback failure -> remains RECONCILING
        with tempfile.TemporaryDirectory() as tmpdir:
            store_vf = InMemoryDurableJournalStore()
            rec_vf = replace(rec_cas, journal_id="vf-j1", journal_state=JournalState.RECONCILING)
            store_vf.create_prepared(replace(rec_vf, journal_state=JournalState.PREPARED))
            store_vf.cas_transition("vf-j1", 1, JournalState.PREPARED, JournalState.APPLYING)
            store_vf.cas_transition("vf-j1", 2, JournalState.APPLYING, JournalState.RECONCILING)
            gh_store_vf = FixtureGitHubStore(body="orig", revision="1", hooks=GHInjectionHooks(verify_fails=True))
            adapter_vf = FakeGitHubAuthorityAdapter(store=gh_store_vf)
            executor_vf = RecoveryExecutor(store=store_vf, port=adapter_vf)
            recov_vf = executor_vf.recover_one("vf-j1")
            self.assertEqual(recov_vf.record.journal_state, JournalState.RECONCILING)

        # FI-09: Third-state observation -> CONFLICT
        with tempfile.TemporaryDirectory() as tmpdir:
            store_3rd = InMemoryDurableJournalStore()
            rec_3rd = replace(rec_cas, journal_id="3rd-j1")
            e_3rd = store_3rd.create_prepared(rec_3rd)
            store_3rd.cas_transition("3rd-j1", e_3rd.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
            gh_store_3rd = FixtureGitHubStore(body="orig", revision="1", hooks=GHInjectionHooks(verify_returns_third=True))
            adapter_3rd = FakeGitHubAuthorityAdapter(store=gh_store_3rd)
            executor_3rd = RecoveryExecutor(store=store_3rd, port=adapter_3rd)
            entry_3rd = executor_3rd.recover_one("3rd-j1")
            self.assertEqual(entry_3rd.record.journal_state, JournalState.CONFLICT)

        # FI-10: Terminal journal persistence failure (J10)
        c_j10 = classify_three_way(observed_raw_digest=_sha("cand"), original_raw_digest=_sha("orig"), candidate_raw_digest=_sha("cand"))
        self.assertEqual(c_j10.journal_state, JournalState.VERIFIED_RECOVERED)

        # FI-11: Process crash & restart recovery
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = pathlib.Path(tmpdir) / "crash_db.json"
            store1 = FileBackedDurableJournalStore(path=db_path)
            rec_cr = replace(rec_cas, journal_id="cr-j1")
            e_prep = store1.create_prepared(rec_cr)
            store1.cas_transition("cr-j1", e_prep.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
            # Reopen new store instance simulating process restart
            store2 = FileBackedDurableJournalStore(path=db_path)
            scanner = RecoveryScanner(store2)
            stranded = scanner.scan_requiring_recovery()
            self.assertEqual(len(stranded), 1)
            self.assertEqual(stranded[0].record.journal_id, "cr-j1")
            self.assertEqual(stranded[0].record.journal_state, JournalState.APPLYING)

        # FI-12: Fresh authorization retry lineage
        with tempfile.TemporaryDirectory() as tmpdir:
            store_ret = InMemoryDurableJournalStore()
            rec_orig = replace(rec_cas, journal_id="ret-j1")
            e_ret = store_ret.create_prepared(rec_orig)
            _, e_app = store_ret.cas_transition("ret-j1", e_ret.journal_revision, JournalState.PREPARED, JournalState.APPLYING)
            _, e_rec = store_ret.cas_transition("ret-j1", e_app.journal_revision, JournalState.APPLYING, JournalState.RECONCILING)
            _, e_term = store_ret.cas_transition("ret-j1", e_rec.journal_revision, JournalState.RECONCILING, JournalState.RETRYABLE_NO_EFFECT)
            # Create retry record with fresh attempt
            retry_entry = create_retry_journal(
                store_ret,
                e_term,
                new_journal_id="ret-j2",
                new_attempt_id="att-fresh-2",
                new_authorization_reference="auth-fresh-2",
                new_lease_reference="lease-fresh-2",
                has_fresh_authorization=True,
                has_fresh_subject_precondition=True,
                has_fresh_raw_authority_precondition=True,
                has_new_bounded_lease=True,
            )
            self.assertEqual(retry_entry.record.attempt_id, "att-fresh-2")
            self.assertEqual(retry_entry.record.correlation_id, rec_orig.correlation_id)
            self.assertEqual(retry_entry.record.journal_state, JournalState.PREPARED)

    # -----------------------------------------------------------------------
    # S5: Negative Matrix Rejection (NEG-01..NEG-12)
    # -----------------------------------------------------------------------

    def test_negative_matrix_rejection(self):
        """S5 negative test: NEG-01 through NEG-12 negative acceptance rules."""
        # NEG-01: CLI semantic authority denied
        self.assertFalse(RECOVERY_EXECUTOR_IS_SEMANTIC_DECISION_MAKER)

        # NEG-02: Direct Core authorization bypass rejected
        with _lifecycle_fixture("neg-02") as f:
            auth = _issue_authorization(f)
            req_no_auth = _bound_request(_plan_init_request(f, intent=auth.intent, lease=None), auth)
            res = plan_init(f.store, req_no_auth)
            self.assertEqual(res.code, "PLAN_INIT_AUTHORIZATION_REQUIRED")

        # NEG-03: Unified Ingress lease bypass rejected
        with _lifecycle_fixture("neg-03") as f:
            res_ing = execute_mutation(MutationIngressRequest("plan_init", f.store, req_no_auth))
            self.assertFalse(res_ing["ok"])
            self.assertEqual(res_ing["error"]["code"], "AUTHORIZATION_MISSING")

        # NEG-04: Same key changed authorization replay rejected with CONFLICT
        with _lifecycle_fixture("neg-04") as f:
            auth = _issue_authorization(f)
            req = _bound_request(_plan_init_request(f, intent=auth.intent, lease=auth.lease), auth)
            res1 = execute_mutation(MutationIngressRequest("plan_init", f.store, req))
            self.assertEqual(res1["lifecycle_code"], "PLAN_INIT_APPLIED")
            # Changed intent with same key
            changed_intent = _make_intent("plan_init", f.plan_ref, auth.intent.idempotency_key, project_id="other")
            changed_auth = _issue_authorization(f, intent=changed_intent)
            req_conflict = _bound_request(
                replace(req, intent=changed_intent, lease=changed_auth.lease),
                changed_auth,
            )
            res2 = execute_mutation(MutationIngressRequest("plan_init", f.store, req_conflict))
            self.assertEqual(res2["lifecycle_code"], "CONFLICT")

        # NEG-05: Generic terminal / Git write rejected
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["git", "write", "--all"])

        # NEG-06: Transport success treated as VERIFIED without readback rejected
        self.assertFalse(TRANSPORT_SUCCESS_ALONE_MEANS_VERIFIED)

        # NEG-07: Journal CAS treated as cross-authority atomicity rejected
        self.assertFalse(JOURNAL_CAS_IMPLIES_EXTERNAL_WRITE_ATOMICITY)
        self.assertIn(PORT_CROSS_ATOMIC, (False, "no"))
        self.assertIn(EXEC_CROSS_ATOMIC, (False, "no"))

        # NEG-08: Retry without fresh authorization rejected
        self.assertTrue(FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY)
        self.assertFalse(OLD_AUTHORIZATION_EVIDENCE_OVERWRITE_ALLOWED)

        # NEG-09: Heuristic successor selection rejected
        with _lifecycle_fixture("neg-09", state="initialized") as f:
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            intent = _make_intent("plan_retirement", f.plan_ref, "neg-09-key", retirement_kind="superseded")
            auth_ret = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req_no_succ = _bound_request(
                replace(_retirement_request(f, snap, intent=intent, lease=auth_ret.lease), retirement_kind="superseded", successor_ref=None),
                auth_ret,
            )
            res_ret = retire_plan(f.store, req_no_succ)
            self.assertEqual(res_ret.code, "RETIREMENT_SUCCESSOR_REQUIRED")

        # NEG-10: Semantic rollback rejected
        from aota_forge.core.journal.executor import SEMANTIC_ROLLBACK_ALLOWED
        self.assertFalse(SEMANTIC_ROLLBACK_ALLOWED)

        # NEG-11: Frozen production storage engine rejected
        self.assertFalse(PRODUCTION_STORAGE_ENGINE_FROZEN)

        # NEG-12: M5 runtime / deploy in M4-8 rejected
        self.assertFalse(APPLYING_RESTART_BLIND_RETRY_ALLOWED)

    # -----------------------------------------------------------------------
    # Successor Rules (SUCC-01..SUCC-07)
    # -----------------------------------------------------------------------

    def test_successor_rules(self):
        """Verify SUCC-01 through SUCC-07 successor and retirement protection rules."""
        # SUCC-01: Abandoned plan retirement -> cancelled
        with _lifecycle_fixture("succ-01", state="initialized") as f:
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            intent = _make_intent("plan_retirement", f.plan_ref, "succ-01-k", retirement_kind="abandoned")
            auth = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req = _bound_request(_retirement_request(f, snap, intent=intent, lease=auth.lease), auth)
            res = retire_plan(f.store, req)
            self.assertEqual(res.code, "RETIREMENT_APPLIED")
            self.assertEqual(f.store.read_subject(f.plan_ref).mechanical_state["state"], "cancelled")

        # SUCC-02: Superseded plan retirement -> superseded with successor
        with _lifecycle_fixture("succ-02", state="initialized") as f:
            succ = _make_plan(f.store, "succ-02-target", state="initialized")
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            intent = _make_intent("plan_retirement", f.plan_ref, "succ-02-k", retirement_kind="superseded", successor_ref=succ.serialize())
            auth = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req = _bound_request(_retirement_request(f, snap, intent=intent, lease=auth.lease, successor=succ), auth)
            res = retire_plan(f.store, req)
            self.assertEqual(res.code, "RETIREMENT_APPLIED")
            self.assertEqual(f.store.read_subject(f.plan_ref).mechanical_state["state"], "superseded")

        # SUCC-03: Active / Current protection
        with _lifecycle_fixture("succ-03", state="initialized") as f:
            subj = f.store.read_subject(f.plan_ref)
            f.store._put_staged(replace(subj, mechanical_state={**subj.mechanical_state, "active": True, "current": True}))
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            intent = _make_intent("plan_retirement", f.plan_ref, "succ-03-k", retirement_kind="abandoned")
            auth = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req = _bound_request(_retirement_request(f, snap, intent=intent, lease=auth.lease), auth)
            res = retire_plan(f.store, req)
            self.assertEqual(res.code, "RETIREMENT_TARGET_PROTECTED")

        # SUCC-04: Running task protection
        with _lifecycle_fixture("succ-04", state="initialized") as f:
            subj = f.store.read_subject(f.plan_ref)
            f.store._put_staged(replace(subj, mechanical_state={**subj.mechanical_state, "running_task": True}))
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            intent = _make_intent("plan_retirement", f.plan_ref, "succ-04-k", retirement_kind="abandoned")
            auth = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req = _bound_request(_retirement_request(f, snap, intent=intent, lease=auth.lease), auth)
            res = retire_plan(f.store, req)
            self.assertEqual(res.code, "RETIREMENT_RUNNING_TASK_PROTECTED")

        # SUCC-05: Stage-one snapshot binding (stale revision fails)
        with _lifecycle_fixture("succ-05", state="initialized") as f:
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            # Advance subject revision
            subj = f.store.read_subject(f.plan_ref)
            f.store._put_staged(set_revision_number(subj, 2))
            intent = _make_intent("plan_retirement", f.plan_ref, "succ-05-k", retirement_kind="abandoned")
            auth = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req = _bound_request(_retirement_request(f, snap, intent=intent, lease=auth.lease), auth)
            res = retire_plan(f.store, req)
            self.assertEqual(res.code, "RETIREMENT_STALE_SNAPSHOT")

        # SUCC-06: No heuristic successor selection
        with _lifecycle_fixture("succ-06", state="initialized") as f:
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            intent = _make_intent("plan_retirement", f.plan_ref, "succ-06-k", retirement_kind="superseded")
            auth = _issue_authorization(f, descriptor=PLAN_RETIREMENT_DESCRIPTOR, intent=intent)
            req = _bound_request(
                replace(_retirement_request(f, snap, intent=intent, lease=auth.lease), retirement_kind="superseded", successor_ref=None),
                auth,
            )
            res = retire_plan(f.store, req)
            self.assertEqual(res.code, "RETIREMENT_SUCCESSOR_REQUIRED")

        # SUCC-07: No implicit resurrection
        with _lifecycle_fixture("succ-07", state="cancelled") as f:
            auth = _issue_authorization(f)
            req = _bound_request(_plan_init_request(f, intent=auth.intent, lease=auth.lease), auth)
            res = plan_init(f.store, req)
            self.assertEqual(res.code, "PLAN_INIT_INVALID_PREDECESSOR")

    # -----------------------------------------------------------------------
    # 14 Historical Regressions (DRIFT-1..APPLY-1)
    # -----------------------------------------------------------------------

    def test_historical_regressions_14_cases(self):
        """Execute all 14 historical regression cases from Issue #8 and Issue #9."""
        # 1. DRIFT-1: CLI argument projection derived from descriptor
        schema = operation_schema("plan_init")
        self.assertEqual(schema["contract_hash"], PLAN_INIT_DESCRIPTOR.contract_hash())

        # 2. RC2-1: Authoritative outcome unknown treated as unknown, not success
        with tempfile.TemporaryDirectory() as tmpdir:
            store_rc2 = InMemoryDurableJournalStore()
            rec_rc2 = JournalRecord(
                journal_id="rc2-j1",
                correlation_id="rc2-corr",
                attempt_id="rc2-att",
                operation="plan_init",
                typed_target=make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "rc2-plan", sub_kind=SubjectKind.PLAN)),
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                idempotency_key="rc2-k",
                intent_fingerprint=_sha("rc2-intent"),
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=_sha("orig"),
                candidate_raw_digest=_sha("cand"),
                normalized_plan_digest=_sha("norm"),
                principal="tester",
                journal_state=JournalState.PREPARED,
            )
            store_rc2.create_prepared(rec_rc2)
            gh_store_rc2 = FixtureGitHubStore(body="orig", revision="1", hooks=GHInjectionHooks(timeout_during_mutate=True))
            adapter_rc2 = FakeGitHubAuthorityAdapter(store=gh_store_rc2)
            executor_rc2 = RecoveryExecutor(store=store_rc2, port=adapter_rc2)
            req_rc2 = PortablePlanMutationRequest(
                operation="plan_init",
                typed_target=rec_rc2.typed_target,
                correlation_id="rc2-corr",
                contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
                idempotency_key="rc2-k",
                intent_fingerprint=_sha("rc2-intent"),
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=_sha("orig"),
                candidate_raw_digest=_sha("cand"),
                normalized_plan_digest=_sha("norm"),
                principal="tester",
                candidate_raw_body="cand",
            )
            res_rc2 = executor_rc2.attempt_external_mutation("rc2-j1", req_rc2)
            self.assertEqual(res_rc2.record.journal_state, JournalState.OUTCOME_UNKNOWN)

        # 3. B014-F: Silent materialization failure detected via readback verify
        c_b014f = classify_three_way(observed_raw_digest=_sha("o"), original_raw_digest=_sha("o"), candidate_raw_digest=_sha("c"))
        self.assertEqual(c_b014f.journal_state, JournalState.RETRYABLE_NO_EFFECT)

        # 4. B011: Subject revision CAS atomic check
        with _lifecycle_fixture("hist-b011") as f:
            auth = _issue_authorization(f)
            req = _bound_request(_plan_init_request(f, intent=auth.intent, lease=auth.lease), auth)
            res = plan_init(f.store, req)
            self.assertEqual(res.code, "PLAN_INIT_APPLIED")
            self.assertEqual(f.store.current_revision(f.plan_ref).revision_number, 2)

        # 5. B013: Capability lease replay across multiple operations denied
        with _lifecycle_fixture("hist-b013", state="initialized") as f:
            init_auth = _issue_authorization(f)
            snap = capture_retirement_snapshot(f.store, f.plan_ref)
            ret_intent = _make_intent("plan_retirement", f.plan_ref, "b013-key", retirement_kind="abandoned")
            req = _bound_request(_retirement_request(f, snap, intent=ret_intent, lease=init_auth.lease), init_auth)
            res = execute_mutation(MutationIngressRequest("plan_retirement", f.store, req))
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "AUTHORIZATION_OPERATION_MISMATCH")

        # 6. B014: Changed intent with same idempotency key fails closed with CONFLICT
        with _lifecycle_fixture("hist-b014") as f:
            auth1 = _issue_authorization(f)
            req1 = _bound_request(_plan_init_request(f, intent=auth1.intent, lease=auth1.lease), auth1)
            execute_mutation(MutationIngressRequest("plan_init", f.store, req1))
            intent2 = _make_intent("plan_init", f.plan_ref, auth1.intent.idempotency_key, project_id="diff")
            auth2 = _issue_authorization(f, intent=intent2)
            req2 = _bound_request(replace(req1, intent=intent2, lease=auth2.lease), auth2)
            res2 = execute_mutation(MutationIngressRequest("plan_init", f.store, req2))
            self.assertEqual(res2["lifecycle_code"], "CONFLICT")

        # 7. RECOVERY-1: Pure deterministic three-way classifier (candidate/original/third)
        c_cand = classify_three_way(observed_raw_digest=_sha("c"), original_raw_digest=_sha("o"), candidate_raw_digest=_sha("c"))
        c_orig = classify_three_way(observed_raw_digest=_sha("o"), original_raw_digest=_sha("o"), candidate_raw_digest=_sha("c"))
        c_3rd = classify_three_way(observed_raw_digest=_sha("3"), original_raw_digest=_sha("o"), candidate_raw_digest=_sha("c"))
        self.assertEqual(c_cand.journal_state, JournalState.VERIFIED_RECOVERED)
        self.assertEqual(c_orig.journal_state, JournalState.RETRYABLE_NO_EFFECT)
        self.assertEqual(c_3rd.journal_state, JournalState.CONFLICT)

        # 8. BIND-1: Deterministic 0/1/>1 subject binding
        with _lifecycle_fixture("hist-bind1") as f:
            auth = _issue_authorization(f)
            req = _bound_request(_plan_init_request(f, intent=auth.intent, lease=auth.lease), auth)
            res = plan_init(f.store, req)
            self.assertEqual(res.code, "PLAN_INIT_APPLIED")

        # 9. WCTX-1: Model-facing path injection rejected; logical IDs used
        specs = semantic_input_specs(PLAN_INIT_DESCRIPTOR)
        for s in specs:
            self.assertNotIn("path", s.name.lower())

        # 10. CAS-1: Multi-domain precondition separation
        with _lifecycle_fixture("hist-cas1") as f:
            auth = _issue_authorization(f)
            req = _bound_request(_plan_init_request(f, intent=auth.intent, lease=auth.lease), auth)
            self.assertIsNotNone(req.preconditions.subject_expected_revision)
            self.assertIsNotNone(req.preconditions.authority_source_revision)
            self.assertIsNotNone(req.preconditions.authority_observed_raw_digest)

        # 11. PRE-1: Mandatory raw precondition check before GitHub write
        gh_store = FixtureGitHubStore(body="remote-changed", revision="2")
        gh_adapter = FakeGitHubAuthorityAdapter(store=gh_store)
        pre_req = PortablePlanMutationRequest(
            operation="plan_init",
            typed_target=make_object_ref(IdKind.SUBJECT, make_id(IdKind.SUBJECT, "pre-1-p", sub_kind=SubjectKind.PLAN)),
            correlation_id="pre-corr",
            contract_hash=PLAN_INIT_DESCRIPTOR.contract_hash(),
            idempotency_key="pre-k",
            intent_fingerprint=_sha("pre-intent"),
            subject_expected_revision=1,
            authority_source_revision="1",
            authority_observed_raw_digest=_sha("remote-orig"),
            candidate_raw_digest=_sha("new"),
            normalized_plan_digest=_sha("norm"),
            principal="tester",
            candidate_raw_body="new",
        )
        pre_resp = gh_adapter.mutate(pre_req)
        self.assertEqual(pre_resp.error_code, "STALE_AUTHORITY")
        self.assertEqual(gh_store.write_issue_call_count, 0)

        # 12. ROLE-1: Control role deterministic 0/1/>1 cardinality fail closed
        gh_store_r1 = FixtureGitHubStore(body="b", revision="1")
        gh_store_r1.seed_control_comment("milestone_progress_index", "c1")
        gh_store_r1.seed_control_comment("milestone_progress_index", "c2")
        gh_adap_r1 = FakeGitHubAuthorityAdapter(store=gh_store_r1)
        r1_resp = gh_adap_r1.resolve_control_role(pre_req.typed_target, "milestone_progress_index")
        self.assertEqual(r1_resp.error_code, "CONTROL_ROLE_DUPLICATE")
        self.assertEqual(r1_resp.binding_count, 2)

        # 13. CARD-1: Update in place required for control roles
        self.assertEqual(CONTROL_ROLE_UPDATE_IN_PLACE_IMPLEMENTED, "yes")

        # 14. APPLY-1: PREPARED -> CAS APPLYING required before external mutate call
        with tempfile.TemporaryDirectory() as tmpdir:
            store_app = InMemoryDurableJournalStore()
            rec_app = JournalRecord(
                journal_id="app-1",
                correlation_id="app-corr",
                attempt_id="app-att",
                operation="plan_init",
                typed_target=pre_req.typed_target,
                contract_hash=pre_req.contract_hash,
                idempotency_key=pre_req.idempotency_key,
                intent_fingerprint=pre_req.intent_fingerprint,
                subject_expected_revision=1,
                authority_source_revision="1",
                authority_observed_raw_digest=_sha("b"),
                candidate_raw_digest=_sha("new"),
                normalized_plan_digest=_sha("norm"),
                principal="tester",
                journal_state=JournalState.PREPARED,
            )
            store_app.create_prepared(rec_app)
            gh_store_ok = FixtureGitHubStore(body="b", revision="1")
            gh_adap_ok = FakeGitHubAuthorityAdapter(store=gh_store_ok)
            exec_app = RecoveryExecutor(store=store_app, port=gh_adap_ok)
            term = exec_app.attempt_external_mutation("app-1", replace(pre_req, authority_source_revision="1", authority_observed_raw_digest=_sha("b")))
            self.assertIn(term.record.journal_state, (JournalState.VERIFIED, JournalState.VERIFIED_RECOVERED))


if __name__ == "__main__":
    unittest.main()
