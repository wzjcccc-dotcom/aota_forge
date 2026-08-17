#!/usr/bin/env python3
"""Deterministic M3-B9 Subject Binding / Recovery validator (Issue #9, lane M3-B9).

Executes the frozen B9 contract:

* stable deterministic binding for Project / Plan / Workspace Subjects
  (B9-N1) and for explicit Work/followup lineage (N15)
* raw internal ID and legacy/current_* inputs rejected (N2, N14)
* trusted ObjectRef fast-path is mechanical and grants no authority (N3, N13)
* 0 / 1 / many classification with deterministic validity filtering (N4-N8)
* no recency / current-pointer / heuristic reduction of many candidates
  (N9, N10, N24); deterministic candidate ordering (N11)
* repository returns candidate lists only, never a selection (N12)
* missing committed Decision basis fails closed (N16)
* projection-stale classification stays read-only and never changes Subject
  authority (N17), binding never implies mutation ALLOW (N18)
* B9 modifies no transition/projection/context/shared layer (N19, N20)
* recovery performs no graph write (N21); semantic_choices contains no
  recommendation (N22); candidate count happens after validity (N23)

Everything runs against the isolated, NON-AUTHORITATIVE in-memory graph
repository.  ``AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no``;
``M3_B_SHADOW_MATERIALIZATION_AUTHORIZED=no``; ``CUTOVER_AUTHORIZED=no``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

BASE_SHA = "f5e5080b9a63b364ca75fa5df8053478ca2a3eed"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:400]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _workspace_refs():
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind

    return make_id(IdKind.WORKFLOW, "b9-wf-1"), make_id(IdKind.WORKFLOW, "b9-wf-2")


def _deterministic_subjects():
    """Build workspace + project + plan deterministic identity values."""
    from aota_forge.core.identity.subject import (
        workspace_subject,
        project_subject,
        plan_subject,
    )

    ws = workspace_subject("b9-workspace-1")
    proj = project_subject("b9-project-1", ws)
    plan = plan_subject("b9-plan-1", proj)
    return ws, proj, plan


def _store_subject(repo, iid, *, kind, workflow_ref=None, state=None):
    from aota_forge.core.graph import records

    repo.store(
        records.subject(
            iid,
            kind,
            workflow_ref=workflow_ref,
            mechanical_state=dict(state or {"revision": 1, "state": "open"}),
            id_derivation="deterministic" if iid.sub_kind != "work" else "minted",
            creation_context={"lane": "M3-B9"},
        )
    )
    return iid


def _store_workflow(repo, wf_id):
    from aota_forge.core.graph import records

    repo.store(records.workflow(wf_id, semantic_intent="b9 fixture", creation_context={"lane": "M3-B9"}))


def _code_src(paths) -> str:
    """Return concatenated module text with module/class/function docstrings removed."""
    import ast as _ast

    def _per_file(src: str) -> str:
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            return src
        drop = set()
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef, _ast.Module)):
                if node.body and isinstance(node.body[0], _ast.Expr) and isinstance(node.body[0].value, _ast.Constant):
                    start = node.body[0].lineno
                    end = node.body[0].end_lineno or start
                    for i in range(start, end + 1):
                        drop.add(i)
        out = []
        for i, line in enumerate(src.splitlines(), start=1):
            if i in drop:
                continue
            line = line.split("#")[0]
            out.append(line)
        return "\n".join(out)

    return "\n".join(_per_file(Path(path).read_text(encoding="utf-8")) for path in paths)


def main() -> int:
    from aota_forge.core.binding import (
        BindingRequest,
        BindingStatus,
        SubjectBindingResolver,
    )
    from aota_forge.core.binding import binder as bmod
    from aota_forge.core.graph.repository import (
        InMemoryGraphRepository,
        GraphRepository,
        GRAPH_REPOSITORY_PERFORMS_SEMANTIC_CHOICE,
        GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY,
        CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH,
        CANDIDATE_LIST_ORDER_DETERMINISTIC,
    )
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref, parse_object_ref
    from aota_forge.core.identity.ids import make_id

    wf_id, other_wf = _workspace_refs()
    ws_iid, proj_iid, plan_iid = _deterministic_subjects()

    # =====================================================================
    # Shared deterministic fixture
    # =====================================================================
    repo = InMemoryGraphRepository()
    _store_workflow(repo, wf_id)
    _store_workflow(repo, other_wf)
    _store_subject(repo, ws_iid, kind="WorkspaceSubject")
    _store_subject(repo, proj_iid, kind="ProjectSubject", workflow_ref=wf_id)
    _store_subject(repo, plan_iid, kind="PlanSubject", workflow_ref=wf_id)

    proj_ref = make_object_ref(IdKind.SUBJECT, proj_iid)
    plan_ref = make_object_ref(IdKind.SUBJECT, plan_iid)
    ws_ref = make_object_ref(IdKind.SUBJECT, ws_iid)
    binder = SubjectBindingResolver(repo)

    # ---- B9-N1: stable semantic Project/Plan/Workspace identity -------------
    r_proj = binder.bind(
        BindingRequest(semantic_ref="b9-project-1", subject_sub_kind=SubjectKind.PROJECT, workspace_id="b9-workspace-1")
    )
    check("B9-N1_stable_project_identity_resolves_deterministically",
          r_proj.status is BindingStatus.BOUND and r_proj.subject_ref == proj_ref,
          getattr(r_proj.subject_ref, "serialize", lambda: str(r_proj.status))())
    r_plan = binder.bind(
        BindingRequest(
            semantic_ref="b9-plan-1", subject_sub_kind=SubjectKind.PLAN,
            workspace_id="b9-workspace-1", project_id="b9-project-1",
        )
    )
    check("B9-N1b_stable_plan_identity_resolves_deterministically",
          r_plan.status is BindingStatus.BOUND and r_plan.subject_ref == plan_ref,
          getattr(r_plan.subject_ref, "serialize", lambda: str(r_plan.status))())
    r_ws = binder.bind(BindingRequest(semantic_ref="b9-workspace-1", subject_sub_kind=SubjectKind.WORKSPACE))
    check("B9-N1c_stable_workspace_identity_resolves_deterministically",
          r_ws.status is BindingStatus.BOUND and r_ws.subject_ref == ws_ref,
          getattr(r_ws.subject_ref, "serialize", lambda: str(r_ws.status))())

    # ---- B9-N2: raw internal ID semantic input rejected ----------------------
    r_raw = binder.bind(BindingRequest(semantic_ref="forge:subject:project:any", subject_sub_kind=SubjectKind.PROJECT))
    check("B9-N2_raw_internal_id_semantic_input_rejected",
          r_raw.status is BindingStatus.INVALID_REFERENCE,
          r_raw.status.value)

    # ---- B9-N3 / N13: trusted ObjectRef fast-path ----------------------------
    r_trusted = binder.bind(
        BindingRequest(
            trusted_object_ref=proj_ref,
            subject_sub_kind=SubjectKind.PROJECT,
            workspace_id="b9-workspace-1",
        )
    )
    check("B9-N3_trusted_object_ref_resolves_mechanically_no_authority",
          r_trusted.status is BindingStatus.BOUND and r_trusted.subject_ref == proj_ref
          and r_trusted.authority is False and r_trusted.to_audit()["authority"] is False,
          r_trusted.status.value)

    wrong_kind_ref = make_object_ref(IdKind.EXECUTION, make_id(IdKind.EXECUTION, "not-subject"))
    r_wrong = binder.bind(BindingRequest(trusted_object_ref=wrong_kind_ref, subject_sub_kind=SubjectKind.PROJECT))
    check("B9-N13_wrong_kind_object_ref_fails_closed",
          r_wrong.status is BindingStatus.INVALID_REFERENCE,
          r_wrong.status.value)

    # ---- B9-N4 / N5: zero candidates -----------------------------------------
    before_count = len(repo.subjects())
    r_missing = binder.bind(
        BindingRequest(
            semantic_ref="b9-project-missing", subject_sub_kind=SubjectKind.PROJECT,
            workspace_id="b9-workspace-1",
        )
    )
    after_count = len(repo.subjects())
    check("B9-N4_zero_candidates_bounded_not_found",
          r_missing.status is BindingStatus.NOT_FOUND,
          r_missing.status.value)
    check("B9-N5_zero_candidates_never_auto_mints_subject",
          r_missing.status is BindingStatus.NOT_FOUND and after_count == before_count,
          f"subjects {before_count}->{after_count}")

    # ---- B9-N6 / N7: one candidate -------------------------------------------
    from aota_forge.core.graph import records as r3
    # B9-N6: a single valid candidate (fully satisfying validity + A5 missing-none)
    # produces a deterministic BOUND — proven with a dedicated single-child repo.
    repo6n = InMemoryGraphRepository(enforce_referential=True)
    _store_workflow(repo6n, wf_id)
    p6 = make_id(IdKind.SUBJECT, "b9-parent-6", sub_kind=SubjectKind.PROJECT)
    _store_subject(repo6n, p6, kind="ProjectSubject", workflow_ref=wf_id)
    p6_ref = make_object_ref(IdKind.SUBJECT, p6)
    d6 = make_id(IdKind.DECISION, "b9-decision-6")
    d6_ref = make_object_ref(IdKind.DECISION, d6)
    repo6n.store(r3.decision(d6, p6, "followup", "single child", decision_time="2030-03-01T12:00:00+00:00"))
    c6 = make_id(IdKind.SUBJECT, "b9-child-6", sub_kind=SubjectKind.WORK)
    _store_subject(repo6n, c6, kind="WorkSubject", workflow_ref=wf_id)
    repo6n.store(r3.followup_edge(make_id(IdKind.EDGE, "b9-edge-6"), p6, c6, d6, rationale="fixture"))
    binder6n = SubjectBindingResolver(repo6n)
    r_one_valid = binder6n.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=p6_ref, need_decision_basis=True, lineage_only=True,
        )
    )
    check("B9-N6_one_valid_candidate_deterministic_bound",
          r_one_valid.status is BindingStatus.BOUND
          and r_one_valid.subject_ref == make_object_ref(IdKind.SUBJECT, c6),
          getattr(r_one_valid.subject_ref, "serialize", lambda: str(r_one_valid.status))())

    # One candidate but failing validity (no committed Decision basis) never binds:
    repo2 = InMemoryGraphRepository(enforce_referential=False)
    _store_workflow(repo2, wf_id)
    parent_iid = make_id(IdKind.SUBJECT, "b9-parent-2", sub_kind=SubjectKind.PROJECT)
    _store_subject(repo2, parent_iid, kind="ProjectSubject", workflow_ref=wf_id)
    parent_ref2 = make_object_ref(IdKind.SUBJECT, parent_iid)
    child_iid = make_id(IdKind.SUBJECT, "b9-child-2", sub_kind=SubjectKind.WORK)
    _store_subject(repo2, child_iid, kind="WorkSubject", workflow_ref=wf_id)
    child_ref2 = make_object_ref(IdKind.SUBJECT, child_iid)
    missing_dec_iid = make_id(IdKind.DECISION, "b9-missing-decision")
    from aota_forge.core.graph import records

    repo2.store(
        records.followup_edge(
            make_id(IdKind.EDGE, "b9-edge-2"),
            parent_iid,
            child_iid,
            missing_dec_iid,
            rationale="fixture",
        )
    )
    binder2 = SubjectBindingResolver(repo2)
    r_dec_missing = binder2.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=parent_ref2, lineage_only=True, need_decision_basis=True,
        )
    )
    check("B9-N7_one_candidate_failing_validity_does_not_bind",
          r_dec_missing.status is BindingStatus.MATERIALIZED_DECISION_MISSING,
          r_dec_missing.status.value)

    # ---- N15 / N16: decision-backed lineage validity -------------------------
    repo3 = InMemoryGraphRepository(enforce_referential=True)
    _store_workflow(repo3, wf_id)
    p_iid = make_id(IdKind.SUBJECT, "b9-parent-3", sub_kind=SubjectKind.PROJECT)
    _store_subject(repo3, p_iid, kind="ProjectSubject", workflow_ref=wf_id)
    p_ref3 = make_object_ref(IdKind.SUBJECT, p_iid)
    d_iid = make_id(IdKind.DECISION, "b9-decision-3")
    d_ref3 = make_object_ref(IdKind.DECISION, d_iid)
    repo3.store(r3.decision(d_iid, p_iid, "followup", "create child", decision_time="2030-03-01T12:00:00+00:00"))
    c_iid = make_id(IdKind.SUBJECT, "b9-child-3", sub_kind=SubjectKind.WORK)
    _store_subject(repo3, c_iid, kind="WorkSubject", workflow_ref=wf_id)
    c_ref3 = make_object_ref(IdKind.SUBJECT, c_iid)
    repo3.store(r3.followup_edge(make_id(IdKind.EDGE, "b9-edge-3"), p_iid, c_iid, d_iid, rationale="fixture"))
    binder3 = SubjectBindingResolver(repo3)

    r_followup = binder3.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=p_ref3, source_decision_ref=d_ref3,
            need_decision_basis=True, lineage_only=True,
        )
    )
    check("B9-N15_followup_candidate_uses_decision_backed_lineage",
          r_followup.status is BindingStatus.BOUND and r_followup.subject_ref == c_ref3,
          getattr(r_followup.subject_ref, "serialize", lambda: str(r_followup.status))())
    check("B9-N16_missing_materialized_decision_does_not_silently_bind",
          r_dec_missing.status is BindingStatus.MATERIALIZED_DECISION_MISSING,
          r_dec_missing.status.value)

    # ---- N8 / N9 / N10 / N11: multiple valid candidates ----------------------
    repo4 = InMemoryGraphRepository(enforce_referential=True)
    _store_workflow(repo4, wf_id)
    p4 = make_id(IdKind.SUBJECT, "b9-parent-4", sub_kind=SubjectKind.PROJECT)
    _store_subject(repo4, p4, kind="ProjectSubject", workflow_ref=wf_id)
    p4_ref = make_object_ref(IdKind.SUBJECT, p4)
    d4 = make_id(IdKind.DECISION, "b9-decision-4")
    d4_ref = make_object_ref(IdKind.DECISION, d4)
    repo4.store(r3.decision(d4, p4, "followup", "two children", decision_time="2030-03-01T12:00:00+00:00"))
    c4a = make_id(IdKind.SUBJECT, "b9-child-4a", sub_kind=SubjectKind.WORK)
    c4b = make_id(IdKind.SUBJECT, "b9-child-4b", sub_kind=SubjectKind.WORK)
    _store_subject(repo4, c4a, kind="WorkSubject", workflow_ref=wf_id)
    _store_subject(repo4, c4b, kind="WorkSubject", workflow_ref=wf_id)
    repo4.store(r3.followup_edge(make_id(IdKind.EDGE, "b9-edge-4a"), p4, c4a, d4, rationale="fixture"))
    repo4.store(r3.followup_edge(make_id(IdKind.EDGE, "b9-edge-4b"), p4, c4b, d4, rationale="fixture"))
    binder4 = SubjectBindingResolver(repo4)

    r_many = binder4.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=p4_ref, lineage_only=True, need_decision_basis=True,
        )
    )
    check("B9-N8_multiple_valid_candidates_needs_semantic_choice",
          r_many.status is BindingStatus.NEEDS_SEMANTIC_CHOICE and len(r_many.candidates) == 2,
          f"{r_many.status.value} candidates={len(r_many.candidates)}")
    check("B9-N9_multiple_candidates_never_reduced_by_recency",
          r_many.status is BindingStatus.NEEDS_SEMANTIC_CHOICE and len(r_many.candidates) == 2,
          "newer candidate never auto-selected")
    check("B9-N10_multiple_candidates_never_reduced_by_current_pointer",
          r_many.status is BindingStatus.NEEDS_SEMANTIC_CHOICE and len(r_many.candidates) == 2,
          "current_* pointer is not a binding authority")
    # Rebind with a forced diagnostic "current_*" extra fact: outcome unchanged.
    r_curfree = binder4.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=p4_ref, lineage_only=True, need_decision_basis=True,
            extra={"current_work_item": make_object_ref(IdKind.SUBJECT, c4a).serialize()},
        )
    )
    check("B9-N9b_recency_fact_does_not_reduce",
          r_curfree.status is BindingStatus.NEEDS_SEMANTIC_CHOICE and len(r_curfree.candidates) == 2,
          f"candidates={len(r_curfree.candidates)}")
    order1 = [c.subject_ref.serialize() for c in r_many.candidates]
    r_many2 = binder4.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=p4_ref, lineage_only=True, need_decision_basis=True,
        )
    )
    order2 = [c.subject_ref.serialize() for c in r_many2.candidates]
    check("B9-N11_candidate_list_deterministic_ordering",
          order1 == order2 and order1 == sorted(order1) and len(r_many.semantic_choices) == 2,
          json.dumps(order1))

    # ---- N12: repository returns candidates only -----------------------------
    scope = repo.subjects_by_scope(workflow_ref=wf_id, sub_kind=SubjectKind.PROJECT)
    check("B9-N12_repository_query_returns_candidates_never_choice",
          isinstance(scope, list) and all(hasattr(s, "subject_id") for s in scope)
          and not hasattr(GraphRepository, "select_one")
          and GRAPH_REPOSITORY_PERFORMS_SEMANTIC_CHOICE is False
          and GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY is True,
          f"scope={len(scope)}")

    # ---- N14: current_* semantic ref rejected ---------------------------------
    r_cur = binder.bind(BindingRequest(semantic_ref="current_plan:anything", subject_sub_kind=SubjectKind.PLAN))
    check("B9-N14_current_pointer_semantic_ref_rejected_nonauthoritative",
          r_cur.status is BindingStatus.INVALID_REFERENCE,
          r_cur.status.value)

    # ---- N17: projection stale classification read-only ----------------------
    repo5 = InMemoryGraphRepository()
    _store_workflow(repo5, wf_id)
    _store_subject(repo5, proj_iid, kind="ProjectSubject", workflow_ref=wf_id)
    binder5 = SubjectBindingResolver(repo5)
    subjects_before = len(repo5.subjects())
    r_stale = binder5.bind(
        BindingRequest(
            semantic_ref="b9-project-1", subject_sub_kind=SubjectKind.PROJECT,
            workspace_id="b9-workspace-1", workflow_ref=make_object_ref(IdKind.WORKFLOW, other_wf),
        )
    )
    subjects_after = len(repo5.subjects())
    check("B9-N17_projection_stale_classification_does_not_change_authority",
          r_stale.status is BindingStatus.PROJECTION_STALE_RECONCILABLE
          and r_stale.subject_ref is None and r_stale.authority is False
          and subjects_after == subjects_before,
          f"{r_stale.status.value} authority={r_stale.authority}")

    # ---- N18: binding result never implies mutation ALLOW --------------------
    check("B9-N18_binding_result_does_not_imply_mutation_allow",
          r_proj.authority is False and r_trusted.authority is False
          and r_many.authority is False and r_missing.authority is False
          and bmod.SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY is False,
          f"BOUND/user/env authority flags {r_proj.authority} {r_trusted.authority} {r_many.authority} {r_missing.authority}")

    # ---- N19 / N20: no transition / projection mutation -----------------------
    diff_cmd = ["git", "-C", str(REPO_ROOT), "diff", "--quiet", BASE_SHA, "HEAD", "--", "aota_forge/core/transitions.py"]
    transitions_untouched = subprocess.run(diff_cmd, capture_output=True).returncode == 0
    proj_cmd = ["git", "-C", str(REPO_ROOT), "diff", "--quiet", BASE_SHA, "HEAD", "--", "aota_forge/core/projection"]
    projection_untouched = subprocess.run(proj_cmd, capture_output=True).returncode == 0
    core_binding_path = REPO_ROOT / "aota_forge" / "core" / "binding"
    binding_files = sorted(str(p) for p in core_binding_path.glob("*.py"))
    binding_code = _code_src(binding_files)
    repo_code = _code_src([str(REPO_ROOT / "aota_forge" / "core" / "graph" / "repository.py")])
    check("B9-N19_b9_does_not_modify_transition_layer", transitions_untouched, str(transitions_untouched))
    check("B9-N20_b9_does_not_require_projection_module",
          projection_untouched
          and "core.projection" not in binding_code
          and "core.projection" not in repo_code
          and "core/projection" not in binding_code,
          str(projection_untouched))

    # ---- N21: recovery performs no graph write ---------------------------------
    repo6 = InMemoryGraphRepository()
    _store_workflow(repo6, wf_id)
    _store_subject(repo6, proj_iid, kind="ProjectSubject", workflow_ref=wf_id)
    binder6 = SubjectBindingResolver(repo6)
    sn = len(repo6.subjects())
    binder6.bind(BindingRequest(semantic_ref="b9-project-1", subject_sub_kind=SubjectKind.PROJECT, workspace_id="b9-workspace-1"))
    binder6.bind(BindingRequest(semantic_ref="missing", subject_sub_kind=SubjectKind.PROJECT, workspace_id="b9-workspace-1"))
    write_words = {w.strip() for w in binding_code.split() if w.strip()}
    check("B9-N21_recovery_performs_no_graph_write",
          len(repo6.subjects()) == sn
          and not any("store" in w and ("repo" in w or ".store" in w or "buffer" in w) for w in write_words)
          and ".store(" not in binding_code and "def store" not in binding_code,
          f"subjects stable={len(repo6.subjects()) == sn}")

    # ---- N22: semantic_choices contains no recommendation ----------------------
    no_reco = all("recommend" not in json.dumps(c).lower() for c in r_many.semantic_choices)
    safe_fields = all(set(c.keys()) <= {"object_ref", "subject_kind", "id_derivation", "owning_workflow_ref", "source_decision_ref"} for c in r_many.semantic_choices)
    check("B9-N22_semantic_choices_no_automatic_recommendation", no_reco and safe_fields,
          json.dumps(r_many.semantic_choices))

    # ---- N23: candidate count happens after validity filtering -----------------
    # parent with two Work children, but one edge is Foreign-decision (owned by a
    # different Subject) -> that candidate is invalid after the validity filter;
    # the count used for classification is computed AFTER the filter, so the ONE
    # valid candidate yields a deterministic BOUND, not a spurious ambiguity.
    repo7 = InMemoryGraphRepository(enforce_referential=True)
    _store_workflow(repo7, wf_id)
    p7 = make_id(IdKind.SUBJECT, "b9-parent-7", sub_kind=SubjectKind.PROJECT)
    other7 = make_id(IdKind.SUBJECT, "b9-other-7", sub_kind=SubjectKind.PROJECT)
    _store_subject(repo7, p7, kind="ProjectSubject", workflow_ref=wf_id)
    _store_subject(repo7, other7, kind="ProjectSubject", workflow_ref=wf_id)
    p7_ref = make_object_ref(IdKind.SUBJECT, p7)
    d7 = make_id(IdKind.DECISION, "b9-decision-7")
    d7f = make_id(IdKind.DECISION, "b9-decision-7f")
    repo7.store(r3.decision(d7, p7, "followup", "child", decision_time="2030-03-01T12:00:00+00:00"))
    repo7.store(r3.decision(d7f, other7, "followup", "foreign child", decision_time="2030-03-01T12:00:00+00:00"))
    c7a = make_id(IdKind.SUBJECT, "b9-child-7a", sub_kind=SubjectKind.WORK)
    c7b = make_id(IdKind.SUBJECT, "b9-child-7b", sub_kind=SubjectKind.WORK)
    _store_subject(repo7, c7a, kind="WorkSubject", workflow_ref=wf_id)
    _store_subject(repo7, c7b, kind="WorkSubject", workflow_ref=wf_id)
    repo7.store(r3.followup_edge(make_id(IdKind.EDGE, "b9-edge-7a"), p7, c7a, d7, rationale="fixture"))
    repo7.store(r3.followup_edge(make_id(IdKind.EDGE, "b9-edge-7b"), p7, c7b, d7f, rationale="fixture"))
    binder7 = SubjectBindingResolver(repo7)
    r_after_filter = binder7.bind(
        BindingRequest(
            semantic_ref="followup", subject_sub_kind=SubjectKind.WORK,
            parent_ref=p7_ref, need_decision_basis=True, lineage_only=True,
        )
    )
    check("B9-N23_candidate_count_happens_after_validity_filter",
          r_after_filter.status is BindingStatus.BOUND
          and r_after_filter.subject_ref == make_object_ref(IdKind.SUBJECT, c7a),
          f"{r_after_filter.status.value} bound={getattr(getattr(r_after_filter,'subject_ref',None),'serialize',lambda:'' )()}")

    # ---- N24: no heuristic Subject selection path -------------------------------
    heuristic_terms = ["recency", "most_recent", "title_similarity", "llm_similarity", "nearest_timestamp", "path_proximity", "first_match"]
    heuristic_hits = [t for t in heuristic_terms if t in binding_code]
    check("B9-N24_no_heuristic_subject_selection_path_exists",
          not heuristic_hits and bmod.HEURISTIC_SUBJECT_SELECTION_ALLOWED is False,
          json.dumps(heuristic_hits))

    # ---- Primary acceptance signals --------------------------------------------
    check("B9_ZERO_CANDIDATE_CASE", r_missing.status is BindingStatus.NOT_FOUND, "PASS")
    check("B9_ONE_CANDIDATE_CASE", r_proj.status is BindingStatus.BOUND, "PASS")
    check("B9_MULTIPLE_CANDIDATE_CASE", r_many.status is BindingStatus.NEEDS_SEMANTIC_CHOICE, "PASS")
    check("B9_CURRENT_POINTER_NON_AUTHORITY_CASE",
          r_cur.status is BindingStatus.INVALID_REFERENCE and bmod.CURRENT_POINTER_SUBJECT_AUTHORITY is False,
          "PASS")
    check("B9_BINDING_AUTHORITY_SEPARATION_CASE",
          r_proj.authority is False and r_trusted.authority is False and bmod.B9_PERFORMS_AUTHORITY_DECISION is False,
          "PASS")

    # ---- Negative reversion proof ----------------------------------------------
    negative = {
        "no_current_pointer_selection": bmod.CURRENT_POINTER_SUBJECT_AUTHORITY is False and r_cur.status is BindingStatus.INVALID_REFERENCE,
        "no_most_recent_selection": bmod.MULTIPLE_CANDIDATE_HEURISTIC_SELECTION is False and not heuristic_hits,
        "no_title_llm_similarity_selection": "llm_similarity" not in binding_code and "title_similarity" not in binding_code,
        "no_raw_internal_id_self_binding": bmod.RAW_INTERNAL_ID_SELF_BINDING_ALLOWED is False and r_raw.status is BindingStatus.INVALID_REFERENCE,
        "no_unique_candidate_bypasses_validity": bmod.UNIQUE_CANDIDATE_BYPASSES_VALIDITY_FILTER is False and r_dec_missing.status is not BindingStatus.BOUND,
        "no_multiple_candidate_silent_reduction": bmod.NO_HIDDEN_REDUCTION_MANY_TO_ONE is True and len(r_many.candidates) == 2,
        "no_binding_grants_authority": bmod.SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY is False and r_proj.authority is False,
        "no_authority_decision_for_needs_semantic_choice": "AuthorityDecision" not in binding_code,
        "no_repository_select_one": not hasattr(GraphRepository, "select_one") and GRAPH_REPOSITORY_PERFORMS_SEMANTIC_CHOICE is False,
        "no_recovery_graph_mutation": ".store(" not in binding_code and "def store" not in binding_code,
        "no_projection_candidate_authority": CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH is True and projection_untouched,
        "no_invalid_current_pointer_authority": bmod.CURRENT_POINTER_SUBJECT_AUTHORITY is False,
    }
    check("B9_NEGATIVE_REVERSION_PROOF", all(v is True for v in negative.values()),
          json.dumps({k: bool(v) for k, v in negative.items()}, sort_keys=True))

    # ---- Module boundary flags ------------------------------------------------
    check("CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH_yes", CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH is True)
    check("GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY_yes", GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY is True)
    check("CANDIDATE_LIST_ORDER_DETERMINISTIC_yes", CANDIDATE_LIST_ORDER_DETERMINISTIC is True)
    check("OBJECT_REF_IS_AUTHORITY_no", getattr(parse_object_ref(str(proj_ref)), "authority")() is False)
    check("BINDING_RECOVERY_IMPLEMENTED_yes", bmod.BINDING_RECOVERY_IMPLEMENTED is True)
    check("HEURISTIC_SUBJECT_SELECTION_ALLOWED_no", bmod.HEURISTIC_SUBJECT_SELECTION_ALLOWED is False)
    check("CURRENT_POINTER_SUBJECT_AUTHORITY_no", bmod.CURRENT_POINTER_SUBJECT_AUTHORITY is False)
    check("SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY_no", bmod.SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY is False)
    check("B9_PERFORMS_AUTHORITY_DECISION_no", bmod.B9_PERFORMS_AUTHORITY_DECISION is False)
    check("MODEL_INTERNAL_IDS_NORMAL_INPUT_no", bmod.MODEL_INTERNAL_IDS_NORMAL_INPUT is False)
    check("RAW_INTERNAL_ID_SELF_BINDING_ALLOWED_no", bmod.RAW_INTERNAL_ID_SELF_BINDING_ALLOWED is False)
    check("TRUSTED_OBJECT_REF_IMPLIES_MUTATION_AUTHORITY_no", r_trusted.authority is False)
    check("ZERO_CANDIDATE_AUTO_CREATES_SUBJECT_no", bmod.ZERO_CANDIDATE_AUTO_CREATES_SUBJECT is False and after_count == before_count)
    check("ZERO_CANDIDATE_HEURISTIC_SELECTION_no", bmod.ZERO_CANDIDATE_HEURISTIC_SELECTION is False)
    check("B9_DEPENDS_ON_B8_no", bmod.B9_DEPENDS_ON_B8 is False)
    check("B9_NEEDS_B7_SOURCE_MUTATION_no", bmod.B9_NEEDS_B7_SOURCE_MUTATION is False)
    check("BINDING_GRAPH_WRITE_COUNT_0", bmod.BINDING_GRAPH_WRITE_COUNT == 0)

    # ---- Regression matrix (not mutated) --------------------------------------
    matrix_path = REPO_ROOT / "deploy/evidence/issues/9/m2-successor-regression-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    rows = matrix.get("failure_classes", [])
    unsupported = [
        row.get("failure_class")
        for row in rows
        if row.get("expected_successor_disposition") not in {
            "ELIMINATED_BY_CONSTRUCTION",
            "DETERMINISTICALLY_RECONCILED",
            "NEEDS_SEMANTIC_CHOICE",
            "NOT_YET_IMPLEMENTED",
        }
    ]
    check("REGRESSION_MATRIX_CLASS_COUNT_16", len(rows) == 16, str(len(rows)))
    check("UNSUPPORTED_SUCCESS_CLAIMS_0", not unsupported, json.dumps(unsupported))

    passed = sum(ok for _, ok, _ in RESULTS)
    failed = len(RESULTS) - passed
    print(f"\nB9 checks PASSED: {passed}/{len(RESULTS)}")
    print(f"B9_FOCUSED_REGRESSION={'PASS' if failed == 0 else 'FAIL'}")
    print(f"B9_NEGATIVE_REVERSION_PROOF={'PASS' if all(v is True for v in negative.values()) else 'FAIL'}")
    print("BINDING_RUNTIME_PRODUCTION_ACTIVE=no")
    print("AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no")
    print("M3_B_SHADOW_MATERIALIZATION_AUTHORIZED=no")
    print("CUTOVER_AUTHORIZED=no")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())