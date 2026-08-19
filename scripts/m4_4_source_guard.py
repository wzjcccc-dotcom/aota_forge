#!/usr/bin/env python3
"""Executable M4-4 mechanical lifecycle source guard."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aota_forge.core.context import (
    PROJECT_STEWARD_AUTO_ACCEPTS_MILESTONE,
    PROJECT_STEWARD_AUTO_CLOSES_PLAN,
    PROJECT_STEWARD_INVENTS_PROJECT_BINDING,
    ProjectBinding,
    prepare_project_binding,
)
from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_DESCRIPTOR,
    PLAN_RETIREMENT_DESCRIPTOR,
)
from aota_forge.core.contracts.mutation import MutationEffect, MutationIntent, MutationPreconditions
from aota_forge.core.contracts.results import lifecycle_envelope
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.regression.fixtures import (
    TempWorkspaceFixture,
    fixture_time,
    make_test_context,
    make_test_lease,
    make_test_store,
    seed_test_subject,
)
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.core.revision import set_revision_number
from aota_forge.core.transitions import (
    M4_4_CAPABILITY_LEASE_ISSUER_IMPLEMENTED,
    M4_4_DURABLE_JOURNAL_IMPLEMENTED,
    M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED,
    M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED,
    PlanInitRequest,
    PlanRetirementRequest,
    capture_retirement_snapshot,
    plan_init,
    retire_plan,
    resolve_retirement_candidates,
)


focused = 0


def check(name: str, condition: bool) -> None:
    global focused
    focused += 1
    if not condition:
        raise AssertionError(name)


def make_plan(store, name: str, state: str = "initialized", **extra):
    mechanical = {
        "state": state,
        "authority_source_revision": "7",
        "authority_observed_raw_digest": "a" * 64,
    }
    mechanical.update(extra)
    subject_id = make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN)
    return seed_test_subject(store, subject_id, kind="plan", mechanical_state=mechanical)


def preconditions(revision: int = 1, source_revision: str = "7", digest: str = "a" * 64):
    return MutationPreconditions(
        subject_expected_revision=revision,
        authority_source_revision=source_revision,
        authority_observed_raw_digest=digest,
    )


def intent(operation: str, target, key: str, **semantic):
    return MutationIntent(
        operation=operation,
        semantic_inputs=semantic,
        logical_target=target.serialize(),
        mutation_scope={"aggregate": "plan_subject"},
        idempotency_key=key,
    )


def retirement_request(context, plan_ref, snapshot, key: str, kind: str, successor_ref=None, *, with_lease=True):
    semantic = {"retirement_kind": kind}
    if successor_ref is not None:
        semantic["successor_ref"] = successor_ref.serialize()
    lease = (
        make_test_lease(
            operation="plan_retirement",
            target_ref=plan_ref,
            expected_revision=snapshot.source_revision,
            lease_id=key,
        )
        if with_lease
        else None
    )
    return PlanRetirementRequest(
        trusted_context=context,
        plan_ref=plan_ref,
        snapshot=snapshot,
        intent=intent("plan_retirement", plan_ref, key, **semantic),
        preconditions=preconditions(snapshot.source_revision),
        trusted_time=fixture_time(),
        retirement_kind=kind,
        successor_ref=successor_ref,
        lease=lease,
    )


def main() -> int:
    context = make_test_context()
    now = fixture_time()

    with TempWorkspaceFixture(prefix="m4-4-source-") as workspace:
        workspace.create_project("p1")
        registry = workspace.create_registry("w1")
        unique_evidence = resolve_project_candidates("w1", registry, "p1")
        missing_evidence = resolve_project_candidates("w1", registry, "missing")
        ambiguous_evidence = unique_evidence
        candidate = unique_evidence.candidates[0]
        binding = ProjectBinding(
            workspace_id=candidate.workspace_id,
            workspace_root=candidate.workspace_root,
            project_id=candidate.project_id,
            project_root=candidate.project_root,
            manifest_path=candidate.manifest_path,
            registry_fingerprint=candidate.registry_fingerprint,
            candidate_fingerprint=candidate.candidate_fingerprint,
            semantic_decision_ref="decision:project:p1",
        )

        # T1: valid uninitialized -> initialized.
        init_store = make_test_store()
        init_ref = make_plan(init_store, "plan_t1", "uninitialized")
        init_intent = intent("plan_init", init_ref, "t1-init", project_id="p1", requested_state="initialized")
        init_lease = make_test_lease(
            operation="plan_init",
            target_ref=init_ref,
            expected_revision=1,
            lease_id="m44-t1-init",
        )
        init_result = plan_init(
            init_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=init_ref,
                intent=init_intent,
                preconditions=preconditions(),
                trusted_time=now,
                project_evidence=unique_evidence,
                project_binding=binding,
                lease=init_lease,
            ),
        )
        check(
            "T1 valid PLAN_INIT transition",
            init_result.code == "PLAN_INIT_APPLIED"
            and init_store.read_subject(init_ref).mechanical_state["state"] == "initialized"
            and init_store.current_revision(init_ref).revision_number == 2,
        )

        # T2: both pre-binding and post-binding re-entry are denied and distinct.
        pre_store = make_test_store()
        pre_ref = make_plan(pre_store, "plan_t2_pre", "initialized", binding_state="unbound")
        pre_result = plan_init(
            pre_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=pre_ref,
                intent=intent("plan_init", pre_ref, "t2-pre", project_id="p1"),
                preconditions=preconditions(),
                trusted_time=now,
            ),
        )
        post_result = plan_init(
            init_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=init_ref,
                intent=intent("plan_init", init_ref, "t2-post", project_id="p1"),
                preconditions=preconditions(2),
                trusted_time=now,
            ),
        )
        check(
            "T2 PLAN_INIT re-entry rejected",
            pre_result.code == "PLAN_INIT_ALREADY_INITIALIZED"
            and pre_result.data["reentry_phase"] == "pre_binding"
            and post_result.code == "PLAN_INIT_ALREADY_INITIALIZED"
            and post_result.data["reentry_phase"] == "post_binding"
            and init_store.current_revision(init_ref).revision_number == 2,
        )

        # T3: invalid predecessor has no effect.
        invalid_store = make_test_store()
        invalid_ref = make_plan(invalid_store, "plan_t3", "cancelled")
        invalid_result = plan_init(
            invalid_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=invalid_ref,
                intent=intent("plan_init", invalid_ref, "t3-invalid", project_id="p1"),
                preconditions=preconditions(),
                trusted_time=now,
            ),
        )
        check(
            "T3 invalid predecessor no effect",
            invalid_result.code == "PLAN_INIT_INVALID_PREDECESSOR"
            and invalid_store.read_subject(invalid_ref).mechanical_state["state"] == "cancelled",
        )

        # T4: stale Subject revision has no effect.
        stale_subject_store = make_test_store()
        stale_subject_ref = make_plan(stale_subject_store, "plan_t4", "uninitialized")
        stale_subject = stale_subject_store.read_subject(stale_subject_ref)
        stale_subject_store._put_staged(set_revision_number(stale_subject, 2))
        stale_subject_result = plan_init(
            stale_subject_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=stale_subject_ref,
                intent=intent("plan_init", stale_subject_ref, "t4-stale", project_id="p1"),
                preconditions=preconditions(1),
                trusted_time=now,
                project_evidence=unique_evidence,
                project_binding=binding,
                lease=make_test_lease(
                    operation="plan_init",
                    target_ref=stale_subject_ref,
                    expected_revision=1,
                    lease_id="m44-t4-stale",
                ),
            ),
        )
        check(
            "T4 stale Subject revision no effect",
            stale_subject_result.code == "PLAN_INIT_STALE_SUBJECT_REVISION"
            and stale_subject_store.read_subject(stale_subject_ref).mechanical_state["state"] == "uninitialized",
        )

        stale_authority_store = make_test_store()
        stale_authority_ref = make_plan(stale_authority_store, "plan_t4_auth", "uninitialized")
        stale_authority_result = plan_init(
            stale_authority_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=stale_authority_ref,
                intent=intent("plan_init", stale_authority_ref, "t4-auth", project_id="p1"),
                preconditions=preconditions(source_revision="8"),
                trusted_time=now,
                project_evidence=unique_evidence,
                project_binding=binding,
                lease=make_test_lease(
                    operation="plan_init",
                    target_ref=stale_authority_ref,
                    expected_revision=1,
                    lease_id="m44-t4-auth",
                ),
            ),
        )
        check("T4 stale authority precondition no effect", stale_authority_result.code == "PLAN_INIT_STALE_AUTHORITY_PRECONDITION")

        # T5: missing exact Project Binding has no effect.
        missing_binding_store = make_test_store()
        missing_binding_ref = make_plan(missing_binding_store, "plan_t5", "uninitialized")
        missing_binding_result = plan_init(
            missing_binding_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=missing_binding_ref,
                intent=intent("plan_init", missing_binding_ref, "t5-binding", project_id="p1"),
                preconditions=preconditions(),
                trusted_time=now,
                project_evidence=unique_evidence,
                lease=make_test_lease(
                    operation="plan_init",
                    target_ref=missing_binding_ref,
                    expected_revision=1,
                    lease_id="m44-t5-binding",
                ),
            ),
        )
        check(
            "T5 missing Project Binding no effect",
            missing_binding_result.code == "PROJECT_BINDING_REQUIRED"
            and missing_binding_store.read_subject(missing_binding_ref).mechanical_state["state"] == "uninitialized",
        )

        # T6: multiple candidates remain a semantic choice; zero is bounded notfound.
        ambiguous_parent = workspace.workdir / "ambiguous"
        ambiguous_parent.mkdir()
        workspace.create_project("p1", parent_dir=ambiguous_parent)
        ambiguous_evidence = resolve_project_candidates("w1", registry, "p1")
        ambiguous_store = make_test_store()
        ambiguous_ref = make_plan(ambiguous_store, "plan_t6", "uninitialized")
        ambiguous_result = plan_init(
            ambiguous_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=ambiguous_ref,
                intent=intent("plan_init", ambiguous_ref, "t6-choice", project_id="p1"),
                preconditions=preconditions(),
                trusted_time=now,
                project_evidence=ambiguous_evidence,
                lease=make_test_lease(
                    operation="plan_init",
                    target_ref=ambiguous_ref,
                    expected_revision=1,
                    lease_id="m44-t6-choice",
                ),
            ),
        )
        check(
            "T6 multiple Project candidates need semantic choice",
            ambiguous_evidence.status == "NEEDS_SEMANTIC_CHOICE"
            and ambiguous_result.code == "NEEDS_SEMANTIC_CHOICE"
            and ambiguous_result.mutation_effect is MutationEffect.NEEDS_SEMANTIC_CHOICE,
        )
        check("T6 zero candidates are bounded PROJECT_NOT_FOUND", missing_evidence.status == "PROJECT_NOT_FOUND")

        # T7: relationship evidence never becomes a Project Binding.
        relationship_store = make_test_store()
        relationship_ref = make_plan(relationship_store, "plan_t7", "uninitialized")
        relationship_result = plan_init(
            relationship_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=relationship_ref,
                intent=intent("plan_init", relationship_ref, "t7-relationship", project_id="p1"),
                preconditions=preconditions(),
                trusted_time=now,
                project_evidence=unique_evidence,
                lease=make_test_lease(
                    operation="plan_init",
                    target_ref=relationship_ref,
                    expected_revision=1,
                    lease_id="m44-t7-relationship",
                ),
            ),
        )
        check(
            "T7 relationship evidence is not Project Binding",
            relationship_result.code == "PROJECT_BINDING_REQUIRED"
            and not relationship_store.read_subject(relationship_ref).mechanical_state.get("project_binding"),
        )

        # T8/T9: exact replay and changed semantic intent conflict.
        replay_result = plan_init(
            init_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=init_ref,
                intent=init_intent,
                preconditions=preconditions(2),
                trusted_time=now,
                project_evidence=unique_evidence,
                project_binding=binding,
                lease=init_lease,
            ),
        )
        conflict_result = plan_init(
            init_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=init_ref,
                intent=intent("plan_init", init_ref, "t1-init", project_id="other", requested_state="initialized"),
                preconditions=preconditions(2),
                trusted_time=now,
                project_evidence=unique_evidence,
                project_binding=binding,
                lease=init_lease,
            ),
        )
        check(
            "T8 same intent replay has no duplicate effect",
            replay_result.replayed and replay_result.mutation_effect is MutationEffect.REPLAYED_VERIFIED
            and init_store.current_revision(init_ref).revision_number == 2,
        )
        check("T9 changed intent same idempotency identity conflicts", conflict_result.code == "CONFLICT")

        # T10-T12: bounded retirement candidate cardinality.
        zero_store = make_test_store()
        zero_result = resolve_retirement_candidates(zero_store)
        one_store = make_test_store()
        one_ref = make_plan(one_store, "plan_t11")
        one_result = resolve_retirement_candidates(one_store)
        many_store = make_test_store()
        make_plan(many_store, "plan_t12_a")
        make_plan(many_store, "plan_t12_b")
        many_result = resolve_retirement_candidates(many_store)
        check("T10 retirement candidate 0", zero_result.code == "RETIREMENT_NO_CANDIDATE")
        check(
            "T11 retirement candidate 1",
            one_result.code == "RETIREMENT_CANDIDATE_UNIQUE" and one_result.candidates[0].plan_ref == one_ref.serialize(),
        )
        check(
            "T12 retirement candidates many need semantic choice",
            many_result.code == "RETIREMENT_NEEDS_SEMANTIC_CHOICE"
            and many_result.mutation_effect is MutationEffect.NEEDS_SEMANTIC_CHOICE,
        )

        # T13: abandoned is cancellation, never deletion, with history preserved.
        abandoned_store = make_test_store()
        abandoned_ref = make_plan(
            abandoned_store,
            "plan_t13",
            milestones={"M4": "defined"},
            work_items=["M4-4-SOURCE"],
            evidence_history=["evidence-1"],
        )
        abandoned_snapshot = capture_retirement_snapshot(abandoned_store, abandoned_ref)
        abandoned_result = retire_plan(
            abandoned_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=abandoned_ref,
                snapshot=abandoned_snapshot,
                intent=intent("plan_retirement", abandoned_ref, "t13-abandoned", retirement_kind="abandoned"),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="abandoned",
                lease=make_test_lease(
                    operation="plan_retirement",
                    target_ref=abandoned_ref,
                    expected_revision=1,
                    lease_id="m44-t13-abandoned",
                ),
            ),
        )
        abandoned_state = abandoned_store.read_subject(abandoned_ref).mechanical_state
        check(
            "T13 abandoned becomes cancelled and preserves history",
            abandoned_result.code == "RETIREMENT_APPLIED"
            and abandoned_state["state"] == "cancelled"
            and abandoned_state["milestones"] == {"M4": "defined"}
            and abandoned_state["work_items"] == ["M4-4-SOURCE"]
            and abandoned_state["evidence_history"] == ["evidence-1"],
        )

        # T14/T15: superseded requires one exact non-self successor.
        successor_store = make_test_store()
        successor_target = make_plan(successor_store, "plan_t14_target")
        successor_snapshot = capture_retirement_snapshot(successor_store, successor_target)
        required_result = retire_plan(
            successor_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=successor_target,
                snapshot=successor_snapshot,
                intent=intent("plan_retirement", successor_target, "t14-required", retirement_kind="superseded"),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="superseded",
            ),
        )
        self_result = retire_plan(
            successor_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=successor_target,
                snapshot=successor_snapshot,
                intent=intent("plan_retirement", successor_target, "t15-self", retirement_kind="superseded", successor_ref=successor_target.serialize()),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="superseded",
                successor_ref=successor_target,
            ),
        )
        check("T14 superseded requires exact successor", required_result.code == "RETIREMENT_SUCCESSOR_REQUIRED")
        check("T15 self-successor is rejected", self_result.code == "RETIREMENT_SELF_SUCCESSOR")

        # T23-T30: superseded successor resolution is exact, fresh, and fail closed.
        valid_superseded_store = make_test_store()
        valid_superseded_target = make_plan(valid_superseded_store, "plan_t23_target")
        valid_superseded_successor = make_plan(valid_superseded_store, "plan_t23_successor")
        valid_superseded_snapshot = capture_retirement_snapshot(valid_superseded_store, valid_superseded_target)
        valid_superseded_result = retire_plan(
            valid_superseded_store,
            retirement_request(
                context,
                valid_superseded_target,
                valid_superseded_snapshot,
                "t23-valid-superseded",
                "superseded",
                valid_superseded_successor,
            ),
        )
        check(
            "T23 unchanged successor applies once",
            valid_superseded_result.code == "RETIREMENT_APPLIED"
            and valid_superseded_store.read_subject(valid_superseded_target).mechanical_state["state"] == "superseded"
            and valid_superseded_store.current_revision(valid_superseded_target).revision_number == 2
            and valid_superseded_store.current_revision(valid_superseded_successor).revision_number == 1
            and len(valid_superseded_snapshot.successor_snapshots) == 1
            and valid_superseded_snapshot.successor_snapshots[0].source_revision == 1
            and bool(valid_superseded_snapshot.successor_snapshots[0].revision_token),
        )

        missing_successor_store = make_test_store()
        missing_successor_target = make_plan(missing_successor_store, "plan_t24_target")
        missing_successor_ref = make_plan(missing_successor_store, "plan_t24_missing")
        missing_successor_store._subjects.pop(missing_successor_ref.internal_id.value)
        missing_successor_snapshot = capture_retirement_snapshot(missing_successor_store, missing_successor_target)
        missing_successor_result = retire_plan(
            missing_successor_store,
            retirement_request(
                context,
                missing_successor_target,
                missing_successor_snapshot,
                "t24-missing-successor",
                "superseded",
                missing_successor_ref,
                with_lease=False,
            ),
        )
        check(
            "T24 missing successor is lifecycle invalid with no effect",
            missing_successor_result.code == "RETIREMENT_SUCCESSOR_INVALID"
            and missing_successor_result.mutation_effect is MutationEffect.NO_EFFECT
            and lifecycle_envelope(missing_successor_result)["lifecycle_code"] == "RETIREMENT_SUCCESSOR_INVALID"
            and lifecycle_envelope(missing_successor_result)["errors"][0]["code"] == "RETIREMENT_SUCCESSOR_INVALID"
            and missing_successor_store.read_subject(missing_successor_target).mechanical_state["state"] == "initialized"
            and missing_successor_store.current_revision(missing_successor_target).revision_number == 1,
        )

        removed_successor_store = make_test_store()
        removed_successor_target = make_plan(removed_successor_store, "plan_t25_target")
        removed_successor_ref = make_plan(removed_successor_store, "plan_t25_successor")
        removed_successor_snapshot = capture_retirement_snapshot(removed_successor_store, removed_successor_target)
        removed_successor_store._subjects.pop(removed_successor_ref.internal_id.value)
        removed_successor_result = retire_plan(
            removed_successor_store,
            retirement_request(
                context,
                removed_successor_target,
                removed_successor_snapshot,
                "t25-removed-successor",
                "superseded",
                removed_successor_ref,
            ),
        )
        check(
            "T25 removed successor after snapshot is stale with no effect",
            removed_successor_result.code == "RETIREMENT_STALE_SNAPSHOT"
            and removed_successor_store.read_subject(removed_successor_target).mechanical_state["state"] == "initialized",
        )

        drift_successor_store = make_test_store()
        drift_successor_target = make_plan(drift_successor_store, "plan_t26_target")
        drift_successor_ref = make_plan(drift_successor_store, "plan_t26_successor")
        drift_successor_snapshot = capture_retirement_snapshot(drift_successor_store, drift_successor_target)
        drift_successor = drift_successor_store.read_subject(drift_successor_ref)
        drift_successor_store._put_staged(set_revision_number(drift_successor, 2))
        drift_successor_result = retire_plan(
            drift_successor_store,
            retirement_request(
                context,
                drift_successor_target,
                drift_successor_snapshot,
                "t26-successor-revision-drift",
                "superseded",
                drift_successor_ref,
            ),
        )
        check(
            "T26 successor revision drift is stale with no effect",
            drift_successor_result.code == "RETIREMENT_STALE_SNAPSHOT"
            and drift_successor_store.read_subject(drift_successor_target).mechanical_state["state"] == "initialized"
            and drift_successor_store.current_revision(drift_successor_ref).revision_number == 2,
        )

        state_drift_successor_store = make_test_store()
        state_drift_successor_target = make_plan(state_drift_successor_store, "plan_t27_target")
        state_drift_successor_ref = make_plan(state_drift_successor_store, "plan_t27_successor")
        state_drift_successor_snapshot = capture_retirement_snapshot(state_drift_successor_store, state_drift_successor_target)
        state_drift_successor = state_drift_successor_store.read_subject(state_drift_successor_ref)
        successor_state = dict(state_drift_successor.mechanical_state)
        successor_state["state"] = "paused"
        state_drift_successor_store._put_staged(replace(state_drift_successor, mechanical_state=successor_state))
        state_drift_successor_result = retire_plan(
            state_drift_successor_store,
            retirement_request(
                context,
                state_drift_successor_target,
                state_drift_successor_snapshot,
                "t27-successor-state-drift",
                "superseded",
                state_drift_successor_ref,
            ),
        )
        check(
            "T27 successor eligibility/state drift is stale with no effect",
            state_drift_successor_result.code == "RETIREMENT_STALE_SNAPSHOT"
            and state_drift_successor_store.read_subject(state_drift_successor_target).mechanical_state["state"] == "initialized",
        )

        replacement_successor_store = make_test_store()
        replacement_successor_target = make_plan(replacement_successor_store, "plan_t28_target")
        replacement_successor_ref = make_plan(replacement_successor_store, "plan_t28_successor")
        replacement_successor_snapshot = capture_retirement_snapshot(replacement_successor_store, replacement_successor_target)
        replacement_successor_new = make_plan(replacement_successor_store, "plan_t28_replacement")
        replacement_successor_result = retire_plan(
            replacement_successor_store,
            retirement_request(
                context,
                replacement_successor_target,
                replacement_successor_snapshot,
                "t28-no-reselection",
                "superseded",
                replacement_successor_ref,
            ),
        )
        check(
            "T28 replacement successor does not trigger reselection",
            replacement_successor_result.code == "RETIREMENT_STALE_SNAPSHOT"
            and replacement_successor_store.read_subject(replacement_successor_target).mechanical_state["state"] == "initialized"
            and replacement_successor_store.read_subject(replacement_successor_new).mechanical_state["state"] == "initialized",
        )

        retirement_replay_store = make_test_store()
        retirement_replay_target = make_plan(retirement_replay_store, "plan_t29_target")
        retirement_replay_successor = make_plan(retirement_replay_store, "plan_t29_successor")
        retirement_replay_snapshot = capture_retirement_snapshot(retirement_replay_store, retirement_replay_target)
        retirement_replay_request = retirement_request(
            context,
            retirement_replay_target,
            retirement_replay_snapshot,
            "t29-retirement-replay",
            "superseded",
            retirement_replay_successor,
        )
        retirement_replay_first = retire_plan(retirement_replay_store, retirement_replay_request)
        retirement_replay_second = retire_plan(retirement_replay_store, retirement_replay_request)
        check(
            "T29 unchanged retirement intent replays without duplicate effect",
            retirement_replay_first.code == "RETIREMENT_APPLIED"
            and retirement_replay_second.code == "RETIREMENT_REPLAYED"
            and retirement_replay_store.current_revision(retirement_replay_target).revision_number == 2,
        )

        retirement_conflict_store = make_test_store()
        retirement_conflict_target = make_plan(retirement_conflict_store, "plan_t30_target")
        retirement_conflict_snapshot = capture_retirement_snapshot(retirement_conflict_store, retirement_conflict_target)
        retirement_conflict_first = retire_plan(
            retirement_conflict_store,
            retirement_request(
                context,
                retirement_conflict_target,
                retirement_conflict_snapshot,
                "t30-changed-intent",
                "abandoned",
            ),
        )
        retirement_conflict_second = retire_plan(
            retirement_conflict_store,
            retirement_request(
                context,
                retirement_conflict_target,
                retirement_conflict_snapshot,
                "t30-changed-intent",
                "superseded",
                with_lease=False,
            ),
        )
        check(
            "T30 changed retirement intent conflicts on same key",
            retirement_conflict_first.code == "RETIREMENT_APPLIED"
            and retirement_conflict_second.code == "CONFLICT"
            and retirement_conflict_store.read_subject(retirement_conflict_target).mechanical_state["state"] == "cancelled",
        )

        # T16/T17: protection is distinct and fail closed.
        active_store = make_test_store()
        active_ref = make_plan(active_store, "plan_t16", active=True, current=True)
        active_snapshot = capture_retirement_snapshot(active_store, active_ref)
        active_result = retire_plan(
            active_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=active_ref,
                snapshot=active_snapshot,
                intent=intent("plan_retirement", active_ref, "t16-active", retirement_kind="abandoned"),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="abandoned",
            ),
        )
        running_store = make_test_store()
        running_ref = make_plan(running_store, "plan_t17", running_task=True)
        running_snapshot = capture_retirement_snapshot(running_store, running_ref)
        running_result = retire_plan(
            running_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=running_ref,
                snapshot=running_snapshot,
                intent=intent("plan_retirement", running_ref, "t17-running", retirement_kind="abandoned"),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="abandoned",
            ),
        )
        check("T16 active/current protection", active_result.code == "RETIREMENT_TARGET_PROTECTED")
        check("T17 running-task protection", running_result.code == "RETIREMENT_RUNNING_TASK_PROTECTED")

        # T18: stale snapshot and changed authority precondition fail closed.
        stale_store = make_test_store()
        stale_ref = make_plan(stale_store, "plan_t18")
        stale_snapshot = capture_retirement_snapshot(stale_store, stale_ref)
        stale_subject = stale_store.read_subject(stale_ref)
        changed_state = dict(stale_subject.mechanical_state)
        changed_state["changed_after_snapshot"] = True
        stale_store._put_staged(replace(stale_subject, mechanical_state=changed_state))
        stale_result = retire_plan(
            stale_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=stale_ref,
                snapshot=stale_snapshot,
                intent=intent("plan_retirement", stale_ref, "t18-stale", retirement_kind="abandoned"),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="abandoned",
            ),
        )
        authority_store = make_test_store()
        authority_ref = make_plan(authority_store, "plan_t18_auth")
        authority_snapshot = capture_retirement_snapshot(authority_store, authority_ref)
        authority_subject = authority_store.read_subject(authority_ref)
        authority_state = dict(authority_subject.mechanical_state)
        authority_state["authority_source_revision"] = "8"
        authority_store._put_staged(replace(authority_subject, mechanical_state=authority_state))
        authority_result = retire_plan(
            authority_store,
            PlanRetirementRequest(
                trusted_context=context,
                plan_ref=authority_ref,
                snapshot=authority_snapshot,
                intent=intent("plan_retirement", authority_ref, "t18-auth", retirement_kind="abandoned"),
                preconditions=preconditions(),
                trusted_time=now,
                retirement_kind="abandoned",
            ),
        )
        check(
            "T18 stale retirement snapshot rejected",
            stale_result.code == "RETIREMENT_STALE_SNAPSHOT"
            and authority_result.code == "RETIREMENT_STALE_AUTHORITY_PRECONDITION",
        )

        # T19: retired Plans cannot be resurrected by retry or PLAN_INIT.
        resurrection_intent = intent("plan_init", abandoned_ref, "t19-resurrection", project_id="p1")
        resurrection_result = plan_init(
            abandoned_store,
            PlanInitRequest(
                trusted_context=context,
                plan_ref=abandoned_ref,
                intent=resurrection_intent,
                preconditions=preconditions(2),
                trusted_time=now,
                project_evidence=unique_evidence,
                project_binding=binding,
                lease=make_test_lease(
                    operation="plan_init",
                    target_ref=abandoned_ref,
                    expected_revision=2,
                    lease_id="m44-t19-resurrection",
                ),
            ),
        )
        check(
            "T19 no implicit resurrection",
            resurrection_result.code == "PLAN_INIT_INVALID_PREDECESSOR"
            and abandoned_store.read_subject(abandoned_ref).mechanical_state["state"] == "cancelled",
        )

        # T20-T22: Project Steward remains a mechanical boundary only.
        try:
            prepare_project_binding(unique_evidence, None)  # type: ignore[arg-type]
        except ValueError:
            steward_invent_result = True
        else:
            steward_invent_result = False
        check(
            "T20 Steward cannot invent Project Binding",
            steward_invent_result and PROJECT_STEWARD_INVENTS_PROJECT_BINDING is False,
        )
        check("T21 Steward cannot auto-accept Milestone", PROJECT_STEWARD_AUTO_ACCEPTS_MILESTONE is False)
        check("T22 Steward cannot auto-close Plan", PROJECT_STEWARD_AUTO_CLOSES_PLAN is False)

        # Contract/result and explicit downstream stop assertions.
        check("lifecycle descriptors are write contracts", PLAN_INIT_DESCRIPTOR.read_write == "write" and PLAN_RETIREMENT_DESCRIPTOR.read_write == "write")
        check("no-effect lifecycle errors remain machine-distinct", lifecycle_envelope(missing_binding_result)["lifecycle_code"] == "PROJECT_BINDING_REQUIRED" and lifecycle_envelope(ambiguous_result)["mutation_effect"] == "NEEDS_SEMANTIC_CHOICE")
        check(
            "M4-2/M4-3/M4-5+ capabilities remain stopped",
            M4_4_CAPABILITY_LEASE_ISSUER_IMPLEMENTED is False
            and M4_4_WRITE_CAPABLE_INGRESS_IMPLEMENTED is False
            and M4_4_EXTERNAL_AUTHORITY_WRITE_IMPLEMENTED is False
            and M4_4_DURABLE_JOURNAL_IMPLEMENTED is False,
        )

    print(f"M4_4_FOCUSED_TEST_COUNT={focused}")
    print("M4_4_B011_SOURCE_BEHAVIOR=PASS")
    print("M4_4_B013_SOURCE_BEHAVIOR=PASS")
    print("M4_4_B014_SOURCE_BEHAVIOR=PASS")
    print("SOURCE_GUARD_REAL_SUCCESSOR_MISSING_CASE=yes")
    print("SOURCE_GUARD_REAL_SUCCESSOR_DRIFT_CASE=yes")
    print("M4_4_SOURCE_GUARD=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        print(f"M4_4_SOURCE_GUARD=FAIL: {exc}")
        raise SystemExit(1)
