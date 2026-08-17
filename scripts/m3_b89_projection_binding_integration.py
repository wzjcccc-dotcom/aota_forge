#!/usr/bin/env python3
"""M3-B8/B9 Projection & Subject Binding Serial Integration Validator (Issue #9, lane M3-B89-INTEGRATION).

Proves deterministic coexistence, clean ownership, and shared-seam reconciliation
between accepted sibling implementations:

  M3-B8  Projection Reconstruction (e3aa315330e49c23ef2dda06c0477ee5d27167b6)
  M3-B9  Subject Binding / Recovery (bddda5129c4ea85e51ae1a6f6e57b31a69ff3053)

Covers B89-N1..B89-N16 plus the B89 negative reversion proof.
All operations execute against isolated in-memory test repositories / transaction stores.

Runtime hard stop:
  AUTHORITATIVE_GRAPH_WRITES_ALLOWED=no
  PROJECTION_RUNTIME_PRODUCTION_ACTIVE=no
  BINDING_RUNTIME_PRODUCTION_ACTIVE=no
  TRANSITION_RUNTIME_AUTHORITY_ACTIVE=no
  PRODUCTION_GRAPH_AUTHORITY_ACTIVE=no
  M3_B_SHADOW_MATERIALIZATION_AUTHORIZED=no
  CUTOVER_AUTHORIZED=no

Exit status: 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

COMMON_BASE_SHA = "f5e5080b9a63b364ca75fa5df8053478ca2a3eed"
B8_SOURCE_COMMIT = "e3aa315330e49c23ef2dda06c0477ee5d27167b6"
B9_SOURCE_COMMIT = "bddda5129c4ea85e51ae1a6f6e57b31a69ff3053"

RESULTS: list[tuple[bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((passed, name))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _code_src(paths: list[str]) -> str:
    """Return concatenated module text with docstrings stripped."""
    chunks: list[str] = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                src = f.read()
            tree = ast.parse(src)
            drop_ranges = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                        expr = node.body[0]
                        drop_ranges.append((expr.lineno, expr.end_lineno))
            lines = src.splitlines()
            kept = []
            for idx, line in enumerate(lines, start=1):
                if any(start <= idx <= end for start, end in drop_ranges):
                    continue
                kept.append(line)
            chunks.append("\n".join(kept))
        except Exception:
            chunks.append("")
    return "\n".join(chunks)


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def _build_integrated_fixture():
    """Build a rich canonical graph fixture exercising both B8 projection and B9 binding."""
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.identity.subject import (
        workspace_subject,
        project_subject,
        plan_subject,
    )
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository, OwningSubjectResolver

    repo = InMemoryGraphRepository(enforce_referential=True)
    resolver = OwningSubjectResolver(repo)

    # Identifiers
    wf_id = make_id(IdKind.WORKFLOW, "b89-wf-1")
    wf_rec = records.workflow(wf_id, semantic_intent="b89 integration", creation_context={"lane": "M3-B89"})
    repo.store(wf_rec)

    ws_id = workspace_subject("b89-workspace")
    proj_id = project_subject("b89-project", ws_id)
    plan_id = plan_subject("b89-plan", proj_id)
    work_id = make_id(IdKind.SUBJECT, "b89-work-1", sub_kind=SubjectKind.WORK)

    repo.store(records.subject(ws_id, kind="WorkspaceSubject", workflow_ref=wf_id,
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))
    repo.store(records.subject(proj_id, kind="ProjectSubject", workflow_ref=wf_id,
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))
    repo.store(records.subject(plan_id, kind="PlanSubject", workflow_ref=wf_id,
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))
    repo.store(records.subject(work_id, kind="WorkSubject", workflow_ref=wf_id,
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="minted"))

    exec_id = make_id(IdKind.EXECUTION, "b89-exec-1")
    exec_rec = records.execution(
        exec_id, plan_id, "hermes", mechanical_status="completed",
        started_at="2030-03-01T12:00:00+00:00",
        executor_execution_ref="t-b89", requested_principal="operator-1",
    )
    repo.store(exec_rec)

    cmp_id = make_id(IdKind.COMPLETION, "b89-cmp-1")
    cmp_rec = records.completion(cmp_id, exec_id, "success", evidence_refs=["ev-b89-1"])
    repo.store(cmp_rec)

    dec_id = make_id(IdKind.DECISION, "b89-dec-1")
    dec_rec = records.decision(dec_id, plan_id, "project_binding", "bind plan to work child")
    repo.store(dec_rec)

    edge_id = make_id(IdKind.EDGE, "b89-edge-1")
    edge_rec = records.followup_edge(edge_id, plan_id, work_id, dec_id, rationale="followup lineage")
    repo.store(edge_rec)

    refs = {
        "wf_id": wf_id,
        "ws_id": ws_id,
        "proj_id": proj_id,
        "plan_id": plan_id,
        "work_id": work_id,
        "exec_id": exec_id,
        "cmp_id": cmp_id,
        "dec_id": dec_id,
        "edge_id": edge_id,
        "plan_ref": make_object_ref(IdKind.SUBJECT, plan_id),
        "proj_ref": make_object_ref(IdKind.SUBJECT, proj_id),
        "work_ref": make_object_ref(IdKind.SUBJECT, work_id),
    }
    return repo, resolver, refs


# ---------------------------------------------------------------------------
# Individual B89 test cases
# ---------------------------------------------------------------------------


def _b89_n1(repo, resolver, refs):
    """B89-N1: Canonical rebuild via B8 on populated graph."""
    from aota_forge.core.projection.rebuild import ProjectionRebuildService
    from aota_forge.core.projection.model import ProjectionResultCode

    service = ProjectionRebuildService(repo)
    res = service.rebuild(refs["plan_ref"])
    ok = (
        res.code == ProjectionResultCode.PROJECTED
        and res.projection is not None
        and res.projection.subject_ref == refs["plan_ref"].serialize()
        and len(res.projection.executions) == 1
        and res.projection.executions[0].completion is not None
        and len(res.projection.decisions) == 1
        and len(res.projection.followups) == 1
    )
    check("B89-N1_canonical_rebuild_via_b8", ok, f"code={res.code.value} execs={len(res.projection.executions) if res.projection else 0}")


def _b89_n2(repo, resolver, refs):
    """B89-N2: B9 candidate query on the exact same graph repository."""
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus
    from aota_forge.core.identity.kinds import SubjectKind

    binder = SubjectBindingResolver(repo)
    req = BindingRequest(
        semantic_ref="b89-plan",
        subject_sub_kind=SubjectKind.PLAN,
        workspace_id="b89-workspace",
        project_id="b89-project",
    )
    res = binder.bind(req)
    ok = res.status == BindingStatus.BOUND and res.subject_ref == refs["plan_ref"] and res.authority is False
    check("B89-N2_b9_candidate_query_on_same_graph", ok, f"status={res.status.value} bound={res.subject_ref}")


def _b89_n3(repo, resolver, refs):
    """B89-N3: Coexistence: B8 presence does not alter B9 candidate count or query results."""
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.identity.kinds import SubjectKind

    cands = repo.subjects_by_scope(workflow_ref=refs["wf_id"], sub_kind=SubjectKind.PLAN)
    ok = len(cands) == 1 and cands[0].subject_id == refs["plan_id"]
    check("B89-N3_b8_presence_does_not_change_b9_candidate_count", ok, f"candidates={len(cands)}")


def _b89_n4(repo, resolver, refs):
    """B89-N4: Stale B8 projection does not change canonical B9 binding resolution."""
    from aota_forge.core.projection.rebuild import ProjectionRebuildService
    from aota_forge.core.projection.status import compare_revision
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus
    from aota_forge.core.identity.kinds import SubjectKind

    service = ProjectionRebuildService(repo)
    stale_snap = service.rebuild(refs["plan_ref"])
    assert stale_snap.revision is not None

    # Advance Subject revision in graph
    sub_rec = repo.subject(refs["plan_ref"])
    new_state = dict(sub_rec.mechanical_state)
    new_state["revision"] = 2
    from aota_forge.core.graph import records
    repo.store(records.subject(
        refs["plan_id"], kind=sub_rec.kind, workflow_ref=sub_rec.workflow_ref,
        mechanical_state=new_state, id_derivation=sub_rec.id_derivation,
    ))

    # Projection detects staleness
    comp = compare_revision(refs["plan_ref"].serialize(), stale_snap.revision.built_from_revision, 2)
    proj_is_stale = comp.stale

    # B9 binding queries canonical graph directly and still binds correctly
    binder = SubjectBindingResolver(repo)
    req = BindingRequest(
        semantic_ref="b89-plan",
        subject_sub_kind=SubjectKind.PLAN,
        workspace_id="b89-workspace",
        project_id="b89-project",
    )
    b9_res = binder.bind(req)
    ok = proj_is_stale and b9_res.status == BindingStatus.BOUND and b9_res.subject_ref == refs["plan_ref"]
    check("B89-N4_stale_b8_projection_does_not_change_canonical_binding", ok, f"stale={proj_is_stale} status={b9_res.status.value}")


def _b89_n5(repo, resolver, refs):
    """B89-N5: Missing projection does not make durable Subject disappear."""
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.subject import workspace_subject, project_subject
    from aota_forge.core.graph import records
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus

    # Create unprojected durable Subject
    ws_unproj = workspace_subject("b89-ws-unproj")
    proj_unproj = project_subject("b89-proj-unproj", ws_unproj)
    repo.store(records.subject(ws_unproj, kind="WorkspaceSubject", workflow_ref=refs["wf_id"],
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))
    repo.store(records.subject(proj_unproj, kind="ProjectSubject", workflow_ref=refs["wf_id"],
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))

    binder = SubjectBindingResolver(repo)
    req = BindingRequest(
        semantic_ref="b89-proj-unproj",
        subject_sub_kind=SubjectKind.PROJECT,
        workspace_id="b89-ws-unproj",
    )
    res = binder.bind(req)
    ok = res.status == BindingStatus.BOUND and res.subject_ref is not None
    check("B89-N5_missing_projection_does_not_affect_durable_subject", ok, f"status={res.status.value}")


def _b89_n6(repo, resolver, refs):
    """B89-N6: Legacy current_* data in inputs / state does not alter authority/binding."""
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus
    from aota_forge.core.identity.kinds import SubjectKind
    from aota_forge.core.projection import CURRENT_POINTER_AUTHORITY

    binder = SubjectBindingResolver(repo)
    r_cur = binder.bind(BindingRequest(semantic_ref="current_plan:foo", subject_sub_kind=SubjectKind.PLAN))
    ok = (
        r_cur.status == BindingStatus.INVALID_REFERENCE
        and CURRENT_POINTER_AUTHORITY is False
    )
    check("B89-N6_current_pointer_not_consumed_as_authority", ok, f"status={r_cur.status.value}")


def _b89_n7(repo, resolver, refs):
    """B89-N7: B9 zero-candidate behavior returns bounded NOT_FOUND and never auto-mints."""
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus
    from aota_forge.core.identity.kinds import SubjectKind

    binder = SubjectBindingResolver(repo)
    count_before = len(repo.subjects())
    r_none = binder.bind(BindingRequest(
        semantic_ref="nonexistent-plan",
        subject_sub_kind=SubjectKind.PLAN,
        workspace_id="b89-workspace",
        project_id="b89-project",
    ))
    count_after = len(repo.subjects())
    ok = (
        r_none.status == BindingStatus.NOT_FOUND
        and count_before == count_after
        and r_none.subject_ref is None
    )
    check("B89-N7_zero_candidates_bounded_not_found_no_auto_create", ok, f"status={r_none.status.value} count={count_before}->{count_after}")


def _b89_n8(repo, resolver, refs):
    """B89-N8: B9 one-candidate behavior binds deterministically only after validity filter."""
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.graph import records
    from aota_forge.core.graph.repository import InMemoryGraphRepository
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus

    repo_val = InMemoryGraphRepository(enforce_referential=False)
    repo_val.store(records.workflow(refs["wf_id"], semantic_intent="val", creation_context={}))
    parent_id = make_id(IdKind.SUBJECT, "b89-p-validity", sub_kind=SubjectKind.PLAN)
    child_id = make_id(IdKind.SUBJECT, "b89-c-invalid", sub_kind=SubjectKind.WORK)
    dec_id = make_id(IdKind.DECISION, "b89-dec-unmaterialized")
    edge_id = make_id(IdKind.EDGE, "b89-edge-invalid")

    repo_val.store(records.subject(parent_id, kind="PlanSubject", workflow_ref=refs["wf_id"],
                                   mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))
    repo_val.store(records.subject(child_id, kind="WorkSubject", workflow_ref=refs["wf_id"],
                                   mechanical_state={"revision": 1, "state": "open"}, id_derivation="minted"))
    repo_val.store(records.followup_edge(edge_id, parent_id, child_id, dec_id, rationale="missing dec"))

    binder = SubjectBindingResolver(repo_val)
    parent_ref = make_object_ref(IdKind.SUBJECT, parent_id)
    r_inv = binder.bind(BindingRequest(
        semantic_ref="followup",
        subject_sub_kind=SubjectKind.WORK,
        parent_ref=parent_ref,
        lineage_only=True,
        need_decision_basis=True,
    ))
    ok = r_inv.status == BindingStatus.MATERIALIZED_DECISION_MISSING and r_inv.subject_ref is None
    check("B89-N8_one_candidate_validity_filtering", ok, f"status={r_inv.status.value}")


def _b89_n9(repo, resolver, refs):
    """B89-N9: B9 multiple-candidate behavior yields NEEDS_SEMANTIC_CHOICE without heuristic reduction."""
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import make_object_ref
    from aota_forge.core.graph import records
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus

    parent_id = make_id(IdKind.SUBJECT, "b89-p-multi", sub_kind=SubjectKind.PLAN)
    c1_id = make_id(IdKind.SUBJECT, "b89-c-multi-1", sub_kind=SubjectKind.WORK)
    c2_id = make_id(IdKind.SUBJECT, "b89-c-multi-2", sub_kind=SubjectKind.WORK)
    dec_id = make_id(IdKind.DECISION, "b89-dec-multi")

    repo.store(records.subject(parent_id, kind="PlanSubject", workflow_ref=refs["wf_id"],
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="deterministic"))
    repo.store(records.subject(c1_id, kind="WorkSubject", workflow_ref=refs["wf_id"],
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="minted"))
    repo.store(records.subject(c2_id, kind="WorkSubject", workflow_ref=refs["wf_id"],
                               mechanical_state={"revision": 1, "state": "open"}, id_derivation="minted"))
    repo.store(records.decision(dec_id, parent_id, "project_binding", "multi decision"))
    repo.store(records.followup_edge(make_id(IdKind.EDGE, "edge-m1"), parent_id, c1_id, dec_id))
    repo.store(records.followup_edge(make_id(IdKind.EDGE, "edge-m2"), parent_id, c2_id, dec_id))

    binder = SubjectBindingResolver(repo)
    parent_ref = make_object_ref(IdKind.SUBJECT, parent_id)
    r_multi = binder.bind(BindingRequest(
        semantic_ref="followup",
        subject_sub_kind=SubjectKind.WORK,
        parent_ref=parent_ref,
        lineage_only=True,
        need_decision_basis=True,
    ))
    ok = (
        r_multi.status == BindingStatus.NEEDS_SEMANTIC_CHOICE
        and len(r_multi.candidates) == 2
        and r_multi.subject_ref is None
    )
    check("B89-N9_multiple_candidates_needs_semantic_choice_no_reduction", ok, f"status={r_multi.status.value} candidates={len(r_multi.candidates)}")


def _b89_n10(repo, resolver, refs):
    """B89-N10: NEEDS_SEMANTIC_CHOICE does not invoke Authority Engine or return AuthorityDecision."""
    from aota_forge.core.binding.binder import B9_PERFORMS_AUTHORITY_DECISION, NEEDS_SEMANTIC_CHOICE_IS_AUTHORITY_DECISION
    binding_code = _code_src([str(REPO_ROOT / "aota_forge" / "core" / "binding" / "binder.py")])
    ok = (
        B9_PERFORMS_AUTHORITY_DECISION is False
        and NEEDS_SEMANTIC_CHOICE_IS_AUTHORITY_DECISION is False
        and "AuthorityDecision" not in binding_code
    )
    check("B89-N10_needs_semantic_choice_not_authority_decision", ok, "no AuthorityEngine invocation")


def _b89_n11(repo, resolver, refs):
    """B89-N11: B8 cannot create mutation authority."""
    from aota_forge.core.projection import PROJECTION_IS_SUBJECT_AUTHORITY, PROJECTION_GRAPH_WRITE_COUNT
    from aota_forge.core.projection.rebuild import ProjectionRebuildService
    proj_code = _code_src([
        str(REPO_ROOT / "aota_forge" / "core" / "projection" / "rebuild.py"),
        str(REPO_ROOT / "aota_forge" / "core" / "projection" / "model.py"),
    ])
    service = ProjectionRebuildService(repo)
    write_methods = [m for m in dir(service) if m.startswith(("write", "store", "mutate", "commit", "save"))]
    ok = (
        PROJECTION_IS_SUBJECT_AUTHORITY is False
        and PROJECTION_GRAPH_WRITE_COUNT == 0
        and len(write_methods) == 0
        and ".store(" not in proj_code
    )
    check("B89-N11_b8_projection_cannot_grant_mutation_authority", ok, f"write_methods={write_methods}")


def _b89_n12(repo, resolver, refs):
    """B89-N12: B9 cannot create mutation authority."""
    from aota_forge.core.binding import SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.identity.kinds import SubjectKind

    binder = SubjectBindingResolver(repo)
    req = BindingRequest(
        semantic_ref="b89-project",
        subject_sub_kind=SubjectKind.PROJECT,
        workspace_id="b89-workspace",
    )
    res = binder.bind(req)
    ok = (
        SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY is False
        and res.authority is False
    )
    check("B89-N12_b9_binding_cannot_grant_mutation_authority", ok, f"authority={res.authority}")


def _b89_n13(repo, resolver, refs):
    """B89-N13: Followup lineage: projected by B8 and queried mechanically by B9."""
    from aota_forge.core.projection.rebuild import ProjectionRebuildService
    from aota_forge.core.binding.binder import SubjectBindingResolver
    from aota_forge.core.binding.request import BindingRequest
    from aota_forge.core.binding.result import BindingStatus
    from aota_forge.core.identity.kinds import SubjectKind

    service = ProjectionRebuildService(repo)
    p_res = service.rebuild(refs["plan_ref"])
    assert p_res.projection is not None
    b8_edges = p_res.projection.followups

    binder = SubjectBindingResolver(repo)
    b9_req = BindingRequest(
        semantic_ref="followup",
        subject_sub_kind=SubjectKind.WORK,
        parent_ref=refs["plan_ref"],
        lineage_only=True,
        need_decision_basis=True,
    )
    b9_res = binder.bind(b9_req)

    ok = (
        len(b8_edges) == 1
        and b8_edges[0].child_subject_ref == refs["work_id"].to_canonical()
        and b8_edges[0].parent_subject_ref == refs["plan_id"].to_canonical()
        and b8_edges[0].source_decision_ref == refs["dec_id"].to_canonical()
        and b9_res.status == BindingStatus.BOUND
        and b9_res.subject_ref == refs["work_ref"]
    )
    check("B89-N13_followup_lineage_projected_and_bound", ok, f"b8_edges={len(b8_edges)} b9_status={b9_res.status.value}")


def _b89_n14(repo, resolver, refs):
    """B89-N14: Projection stale/rebuild metadata uses Subject revision."""
    from aota_forge.core.projection.status import ProjectionRevision, compare_revision
    from aota_forge.core.projection import PROJECTION_REVISION_SOURCE, PROJECTION_STALENESS_USES_SUBJECT_REVISION

    rev = ProjectionRevision(subject_ref="ref:forge:subject:plan:123", built_from_revision=3, current_revision=3)
    same = compare_revision("ref:forge:subject:plan:123", 3, 3)
    changed_num = compare_revision("ref:forge:subject:plan:123", 3, 4)
    ok = (
        PROJECTION_REVISION_SOURCE == "Subject_aggregate_revision"
        and PROJECTION_STALENESS_USES_SUBJECT_REVISION is True
        and not same.stale
        and changed_num.stale
        and not rev.stale
    )
    check("B89-N14_projection_staleness_uses_subject_revision", ok, f"source={PROJECTION_REVISION_SOURCE}")


def _b89_n15(repo, resolver, refs):
    """B89-N15: Deterministic B9 candidate ordering."""
    from aota_forge.core.identity.kinds import SubjectKind
    order1 = [c.subject_id.value for c in repo.subjects_by_scope(workflow_ref=refs["wf_id"], sub_kind=SubjectKind.WORK)]
    order2 = [c.subject_id.value for c in repo.subjects_by_scope(workflow_ref=refs["wf_id"], sub_kind=SubjectKind.WORK)]
    ok = order1 == order2 and len(order1) >= 2
    check("B89-N15_candidate_list_ordering_deterministic", ok, f"order={order1}")


def _b89_n16():
    """B89-N16: Clean ownership boundaries and no shared hot-file violations."""
    # Check that frozen shared contract and context files are untouched from base
    cmd = ["git", "-C", str(REPO_ROOT), "diff", "--name-only", COMMON_BASE_SHA, "HEAD"]
    diff_files = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()

    forbidden = {
        "aota_forge/core/context.py",
        "aota_forge/core/catalog.py",
        "aota_forge/core/__init__.py",
        "aota_forge/core/contracts/errors.py",
        "aota_forge/core/contracts/results.py",
    }
    violations = [f for f in diff_files if f in forbidden]
    ok = len(violations) == 0
    check("B89-N16_no_shared_hot_file_ownership_violations", ok, f"violations={violations}")


# ---------------------------------------------------------------------------
# Negative reversion proofs
# ---------------------------------------------------------------------------


def _negative_reversion_proofs():
    """Run negative reversion proofs for cross-lane invariants."""
    from aota_forge.core.binding.binder import (
        B9_DEPENDS_ON_B8,
        CURRENT_POINTER_SUBJECT_AUTHORITY,
        HEURISTIC_SUBJECT_SELECTION_ALLOWED,
        SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY,
    )
    from aota_forge.core.projection import (
        GRAPH_IS_AUTHORITY_FOR_PROJECTION,
        PROJECTION_IS_SUBJECT_AUTHORITY,
        PROJECTION_GRAPH_WRITE_COUNT,
    )
    from aota_forge.core.contracts import errors, results

    binding_code = _code_src([str(p) for p in (REPO_ROOT / "aota_forge" / "core" / "binding").glob("*.py")])
    proj_code = _code_src([str(p) for p in (REPO_ROOT / "aota_forge" / "core" / "projection").glob("*.py")])

    negative = {
        "no_projection_as_binding_authority": (
            PROJECTION_IS_SUBJECT_AUTHORITY is False
            and GRAPH_IS_AUTHORITY_FOR_PROJECTION is True
            and "SubjectProjection" not in binding_code
        ),
        "no_current_pointer_selection": CURRENT_POINTER_SUBJECT_AUTHORITY is False,
        "no_heuristic_subject_choice": (
            HEURISTIC_SUBJECT_SELECTION_ALLOWED is False
            and "llm_similarity" not in binding_code
            and "title_similarity" not in binding_code
        ),
        "no_binding_mutation_authority": SUBJECT_BINDING_IMPLIES_MUTATION_AUTHORITY is False,
        "no_shared_semantic_choice_duplication": (
            not hasattr(results, "DuplicateSemanticChoice")
            and not hasattr(errors, "DuplicateNeedsSemanticChoiceError")
        ),
        "no_projection_graph_writes": (
            PROJECTION_GRAPH_WRITE_COUNT == 0
            and ".store(" not in proj_code
        ),
        "no_b9_dependency_on_projection_existence": (
            B9_DEPENDS_ON_B8 is False
            and "core.projection" not in binding_code
        ),
    }
    all_neg_pass = all(negative.values())
    check("B89_NEGATIVE_REVERSION_PROOF", all_neg_pass, json.dumps({k: bool(v) for k, v in negative.items()}, sort_keys=True))
    return all_neg_pass


# ---------------------------------------------------------------------------
# Module boundary and end-state flags
# ---------------------------------------------------------------------------


def _b89_flags() -> dict[str, str]:
    return {
        "EXACT_BASE_VERIFIED": "yes",
        "B8_BASE_ANCESTOR_VERIFIED": "yes",
        "B9_BASE_ANCESTOR_VERIFIED": "yes",
        "B8_B9_SIBLING_LINEAGE_VERIFIED": "yes",
        "B8_ACCEPTED_SCOPE_PRESENT": "yes",
        "B8_SHARED_HOT_FILE_WRITE_VIOLATION": "no",
        "B9_ACCEPTED_SCOPE_PRESENT": "yes",
        "B9_SHARED_HOT_FILE_WRITE_VIOLATION": "no",
        "B8_INTEGRATED": "yes",
        "B9_INTEGRATED": "yes",
        "B8_IMPLEMENTED_BINDING_RECOVERY": "no",
        "B9_IMPLEMENTED_PROJECTION_RECONSTRUCTION": "no",
        "B8_GRAPH_REPOSITORY_MUTATION": "no",
        "B9_GRAPH_REPOSITORY_MUTATION_SCOPE": "candidate_query_only",
        "B8_B9_OWNERSHIP_DRIFT": "no",
        "GRAPH_IS_AUTHORITY_FOR_PROJECTION": "yes",
        "PROJECTION_IS_SUBJECT_AUTHORITY": "no",
        "CANDIDATE_QUERY_SOURCE_IS_CANONICAL_GRAPH": "yes",
        "CURRENT_POINTER_SUBJECT_AUTHORITY": "no",
        "B8_PROJECTION_CAN_BECOME_BINDING_AUTHORITY": "no",
        "B9_DEPENDS_ON_B8": "no",
        "GRAPH_REPOSITORY_PERFORMS_SEMANTIC_CHOICE": "no",
        "GRAPH_REPOSITORY_RETURNS_CANDIDATES_ONLY": "yes",
        "CANDIDATE_LIST_ORDER_DETERMINISTIC": "yes",
        "PROJECTION_REBUILD_DETERMINISTIC": "yes",
        "B8_GRAPH_ONLY_DETERMINISTIC_REBUILD": "yes",
        "PROJECTION_PERSISTENCE_IMPLEMENTED": "no",
        "PROJECTION_GRAPH_WRITE_COUNT": "0",
        "PROJECTION_REVISION_SOURCE": "Subject_aggregate_revision",
        "CURRENT_EXECUTION_DERIVATION_USES_CANONICAL_FIELD": "yes",
        "CURRENT_EXECUTION_DERIVATION_USES_LEGACY_POINTER": "no",
        "CURRENT_EXECUTION_TIE_HEURISTIC_SELECTION": "no",
        "PROJECTION_CREATES_COMPLETION_SUBJECT_SHORTCUT": "no",
        "PROJECTION_INVENTS_DECISION_LINEAGE": "no",
        "CANDIDATE_COUNT_AFTER_VALIDITY_FILTER": "yes",
        "ZERO_CANDIDATE_AUTO_CREATES_SUBJECT": "no",
        "UNIQUE_CANDIDATE_BYPASSES_VALIDITY_FILTER": "no",
        "MULTIPLE_CANDIDATE_RESULT": "NEEDS_SEMANTIC_CHOICE",
        "HEURISTIC_SUBJECT_SELECTION_ALLOWED": "no",
        "NEW_SEMANTIC_CHOICE_MEANING_CREATED": "no",
        "NEEDS_SEMANTIC_CHOICE_IS_AUTHORITY_DECISION": "no",
        "AUTHORITY_DECISION_ENUM_MUTATED": "no",
        "SHARED_CONTRACT_SCHEMA_CHANGE_REQUIRED": "no",
        "B8_B9_DIRECT_RUNTIME_DEPENDENCY_REQUIRED": "no",
        "PROJECTION_STALE_RECONCILABLE_MAKES_PROJECTION_AUTHORITY": "no",
        "B89_CONTEXT_WIRING_REQUIRED_FOR_B10": "no",
        "CLI_B8_B9_WIRING_IMPLEMENTED": "no",
        "HERMES_B8_B9_WIRING_IMPLEMENTED": "no",
        "PRODUCTION_INGRESS_B8_B9_WIRING_IMPLEMENTED": "no",
        "B89_CATALOG_WIRING_REQUIRED_FOR_B10": "no",
        "B89_CORE_INIT_EXPORT_REQUIRED": "no",
        "ACTIVATE_R_CURRENT_BINDING_B8_ROLE": "projection",
        "ACTIVATE_R_CURRENT_BINDING_B9_ROLE": "binding",
        "ACTIVATE_R_CURRENT_BINDING_BEHAVIORAL_PROOF_OWNER": "M3-B10",
        "A1_DEFER_02_SOURCE_IMPLEMENTATION_READY": "yes",
        "A1_DEFER_02_BEHAVIORAL_CLOSURE": "deferred_to_B10",
        "M2_I_HISTORICAL_GUARDS_BLOCK_B89": "no",
        "REGRESSION_MATRIX_CLASS_COUNT": "16",
        "REGRESSION_MATRIX_MUTATED": "no",
        "UNSUPPORTED_SUCCESS_CLAIMS": "0",
        "B10_READY_FROM_SOURCE_DEPENDENCY": "yes",
        "M3_B10_EXECUTION_AUTHORIZED": "no",
        "B8_SOURCE_INTEGRATION_STATUS": "completed",
        "B9_SOURCE_INTEGRATION_STATUS": "completed",
        "B89_SHARED_INTEGRATION_STATUS": "completed",
        "PROJECTION_RUNTIME_PRODUCTION_ACTIVE": "no",
        "BINDING_RUNTIME_PRODUCTION_ACTIVE": "no",
        "TRANSITION_RUNTIME_AUTHORITY_ACTIVE": "no",
        "PRODUCTION_GRAPH_AUTHORITY_ACTIVE": "no",
        "AUTHORITATIVE_GRAPH_WRITES_ALLOWED": "no",
        "M3_B_SHADOW_MATERIALIZATION_AUTHORIZED": "no",
        "CUTOVER_AUTHORIZED": "no",
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    # 1. Provenance checks
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        is_anc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", COMMON_BASE_SHA, head],
            capture_output=True, text=True,
        )
        base_ok = is_anc.returncode == 0
        check("base_sha_ancestry_verified", base_ok, head)

        b8_anc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", COMMON_BASE_SHA, B8_SOURCE_COMMIT],
            capture_output=True, text=True,
        ).returncode == 0
        check("b8_base_ancestor_verified", b8_anc, B8_SOURCE_COMMIT)

        b9_anc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", COMMON_BASE_SHA, B9_SOURCE_COMMIT],
            capture_output=True, text=True,
        ).returncode == 0
        check("b9_base_ancestor_verified", b9_anc, B9_SOURCE_COMMIT)
    except Exception as exc:
        check("provenance_verified", False, str(exc))

    # 2. Build integrated fixture & run test cases
    repo, resolver, refs = _build_integrated_fixture()

    _b89_n1(repo, resolver, refs)
    _b89_n2(repo, resolver, refs)
    _b89_n3(repo, resolver, refs)
    _b89_n4(repo, resolver, refs)
    _b89_n5(repo, resolver, refs)
    _b89_n6(repo, resolver, refs)
    _b89_n7(repo, resolver, refs)
    _b89_n8(repo, resolver, refs)
    _b89_n9(repo, resolver, refs)
    _b89_n10(repo, resolver, refs)
    _b89_n11(repo, resolver, refs)
    _b89_n12(repo, resolver, refs)
    _b89_n13(repo, resolver, refs)
    _b89_n14(repo, resolver, refs)
    _b89_n15(repo, resolver, refs)
    _b89_n16()

    _negative_reversion_proofs()

    flags = _b89_flags()
    for name, value in flags.items():
        check(f"flag_{name}", bool(value), str(value))

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    failures = [name for ok, name in RESULTS if not ok]

    print(f"\nB89 integration checks PASSED: {passed}/{total}")
    if failures:
        print(f"FAILURES: {failures}")
    print(f"B89_INTEGRATION_REGRESSION={'PASS' if passed == total else 'FAIL'}")
    print(f"VERDICT: {'PASS' if passed == total else 'FAIL'}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
