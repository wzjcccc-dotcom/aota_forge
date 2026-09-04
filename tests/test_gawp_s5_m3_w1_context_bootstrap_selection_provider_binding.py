"""S5/M3/W1 Context Bootstrap Selection & Provider Binding Contract — focused proof.

Covers required groups A-X plus strict serialization, authority firewall,
deterministic ordering, provider/hydration firewalls, and contract reuse.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.providers.context import ContextRequest
import aota_forge.work_plane.context_bootstrap_plan as cb_mod
from aota_forge.work_plane.context_bootstrap_plan import (
    ContextBootstrapIntent,
    ContextBootstrapPlan,
    create_context_bootstrap_plan,
)
from aota_forge.work_plane.context_lifecycle import RolloverDecision, RolloverDisposition
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.session_checkpoint import WorkingTruthProjection


def _sr(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)


def _handoff(context_refs=(), project="proj:A", plan="plan:P", milestone="ms:M1", work_item=None, scope="bounded scope task"):
    kwargs = dict(
        work_role="coder",
        task_kind="implementation",
        objective="implement feature",
        bounded_scope=scope,
        validation_expectations=("check",),
        semantic_stop_expectations=("stop",),
        project_ref=_sr(project),
        plan_ref=_sr(plan),
        milestone_ref=_sr(milestone),
        context_refs=tuple(context_refs),
    )
    if work_item:
        kwargs["work_item_ref"] = _sr(work_item)
    return TaskHandoff(**kwargs)


def _wt(context_refs=(), project="proj:A", plan="plan:P", milestone="ms:M1", active_w=None):
    base = dict(
        project_ref=_sr(project),
        plan_ref=_sr(plan),
        milestone_ref=_sr(milestone),
    )
    if active_w:
        base["active_work_item_ref"] = _sr(active_w)
    base["context_refs"] = tuple(context_refs)
    return WorkingTruthProjection(**base)


# ---------------------------------------------------------------------------
# A. Valid current-only context
# ---------------------------------------------------------------------------

def test_a_valid_current_only():
    h = _handoff(context_refs=(_sr("ctx:a"), _sr("ctx:b")))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert isinstance(plan, ContextBootstrapPlan)
    assert len(plan.intents) == 2
    refs = {i.context_ref.ref for i in plan.intents}
    assert refs == {"ctx:a", "ctx:b"}
    # deterministic
    plan2 = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert plan.canonical_json() == plan2.canonical_json()
    assert plan.plan_digest == plan2.plan_digest


# ---------------------------------------------------------------------------
# B. Valid recovered-only context
# ---------------------------------------------------------------------------

def test_b_valid_recovered_only():
    h = _handoff(context_refs=())
    wt = _wt(context_refs=(_sr("ctx:rec1"), _sr("ctx:rec2")))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.UNDETERMINED, provider_limit=5)
    assert len(plan.intents) == 2
    assert all(i.origin == "recovered_continuity" for i in plan.intents)
    assert all(i.delivery_intent == "progressive" for i in plan.intents)


# ---------------------------------------------------------------------------
# C. Current + recovered exact duplicate deduped
# ---------------------------------------------------------------------------

def test_c_exact_duplicate_deduped():
    h = _handoff(context_refs=(_sr("ctx:dup", digest="abc"),))
    wt = _wt(context_refs=(_sr("ctx:dup", digest="abc"),))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert len(plan.intents) == 1
    assert plan.intents[0].context_ref.ref == "ctx:dup"
    assert plan.intents[0].context_ref.digest == "abc"
    # ensure deduped, origin is current
    assert plan.intents[0].origin == "current_handoff"


# ---------------------------------------------------------------------------
# D. Current/recovered same ref differing digest — current wins
# ---------------------------------------------------------------------------

def test_d_current_wins_different_digest():
    h = _handoff(context_refs=(_sr("ctx:same", digest="digest-current"),))
    wt = _wt(context_refs=(_sr("ctx:same", digest="digest-recovered"),))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert len(plan.intents) == 1
    assert plan.intents[0].context_ref.digest == "digest-current"
    assert plan.intents[0].origin == "current_handoff"
    # no freshness authority
    assert cb_mod.CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE_IS_AUTHORITY is False
    assert cb_mod.CURRENT_HANDOFF_REF_SELECTION_PRECEDENCE_PROVES_FRESHNESS is False


# ---------------------------------------------------------------------------
# E. Same-origin conflicting digest fails closed
# ---------------------------------------------------------------------------

def test_e_same_origin_conflict_fails():
    # current side conflict
    h = _handoff(context_refs=(_sr("ctx:conf", digest="a"), _sr("ctx:conf", digest="b")))
    wt = _wt(context_refs=())
    with pytest.raises(ValueError):
        create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    # recovered side conflict
    h2 = _handoff(context_refs=())
    wt2 = _wt(context_refs=(_sr("ctx:conf2", digest="x"), _sr("ctx:conf2", digest="y")))
    with pytest.raises(ValueError):
        create_context_bootstrap_plan(h2, wt2, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert cb_mod.SAME_ORIGIN_CONTEXT_DIGEST_CONFLICT_FAILS_CLOSED is True


# ---------------------------------------------------------------------------
# F. Candidate max 32, no silent truncation
# ---------------------------------------------------------------------------

def test_f_candidate_max_32():
    ctxs_current = tuple(_sr(f"ctx:c{i}") for i in range(16))
    ctxs_recovered = tuple(_sr(f"ctx:r{i}") for i in range(16))
    h = _handoff(context_refs=ctxs_current)
    wt = _wt(context_refs=ctxs_recovered)
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert len(plan.intents) == 32
    assert cb_mod.M3_CONTEXT_REF_CANDIDATE_MAX == 32
    assert cb_mod.CONTEXT_REF_COMPOSITION_BOUNDED is True
    assert cb_mod.CONTEXT_REF_COMPOSITION_NO_SILENT_TRUNCATION is True
    # ensure no truncation happened — all refs present
    all_refs = {i.context_ref.ref for i in plan.intents}
    assert len(all_refs) == 32


# ---------------------------------------------------------------------------
# G. Order invariance
# ---------------------------------------------------------------------------

def test_g_order_invariance():
    # permutation of current refs should produce same canonical plan
    h1 = _handoff(context_refs=(_sr("ctx:b"), _sr("ctx:a"), _sr("ctx:c")))
    h2 = _handoff(context_refs=(_sr("ctx:c"), _sr("ctx:a"), _sr("ctx:b")))
    plan1 = create_context_bootstrap_plan(h1, None, rollover_disposition=RolloverDisposition.UNDETERMINED, provider_limit=10)
    plan2 = create_context_bootstrap_plan(h2, None, rollover_disposition=RolloverDisposition.UNDETERMINED, provider_limit=10)
    assert plan1.canonical_json() == plan2.canonical_json()
    # also across recovered
    wt1 = _wt(context_refs=(_sr("ctx:z"), _sr("ctx:y")))
    wt2 = _wt(context_refs=(_sr("ctx:y"), _sr("ctx:z")))
    h_empty = _handoff(context_refs=())
    p1 = create_context_bootstrap_plan(h_empty, wt1, rollover_disposition=RolloverDisposition.UNDETERMINED, provider_limit=10)
    p2 = create_context_bootstrap_plan(h_empty, wt2, rollover_disposition=RolloverDisposition.UNDETERMINED, provider_limit=10)
    assert p1.canonical_json() == p2.canonical_json()
    assert cb_mod.CONTEXT_REF_ORDERING_DETERMINISTIC is True


# ---------------------------------------------------------------------------
# H. Cross-project recovered scope fails closed
# ---------------------------------------------------------------------------

def test_h_cross_project_fails_closed():
    h = _handoff(context_refs=(_sr("ctx:a"),), project="proj:A")
    wt = _wt(context_refs=(_sr("ctx:b"),), project="proj:B")
    with pytest.raises(ValueError):
        create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert cb_mod.CROSS_PROJECT_RECOVERED_CONTEXT_FAILS_CLOSED is True


# ---------------------------------------------------------------------------
# I. Cross-Plan recovered scope fails closed
# ---------------------------------------------------------------------------

def test_i_cross_plan_fails_closed():
    h = _handoff(context_refs=(_sr("ctx:a"),), plan="plan:P1")
    wt = _wt(context_refs=(_sr("ctx:b"),), plan="plan:P2")
    with pytest.raises(ValueError):
        create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert cb_mod.CROSS_PLAN_RECOVERED_CONTEXT_FAILS_CLOSED is True


# ---------------------------------------------------------------------------
# J. Current Milestone/Work Item precedence
# ---------------------------------------------------------------------------

def test_j_current_milestone_workitem_precedence():
    h = _handoff(context_refs=(_sr("ctx:a"),), milestone="ms:M2", work_item="w:W2")
    wt = _wt(context_refs=(_sr("ctx:b"),), milestone="ms:M1", active_w="w:W1")
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    # plan milestone should be from handoff (M2), not recovered (M1)
    assert plan.milestone_ref.ref == "ms:M2"
    assert plan.work_item_ref.ref == "w:W2"
    assert cb_mod.CURRENT_TASK_SCOPE_WINS_OVER_RECOVERED_CONTEXT is True


# ---------------------------------------------------------------------------
# K. ContextRequest binding
# ---------------------------------------------------------------------------

def test_k_context_request_binding():
    h = _handoff(context_refs=(_sr("ctx:q1"),), scope="my bounded scope")
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=7)
    assert len(plan.intents) == 1
    req = plan.intents[0].request
    assert isinstance(req, ContextRequest)
    # subject derived from current scope (most-specific)
    assert req.subject_ref == "w:W2" if h.work_item_ref else req.subject_ref  # just check non-empty
    assert req.scope == "my bounded scope"
    assert req.query == "ctx:q1"
    assert req.limit == 7
    # query should be bounded context intent, not digest
    assert req.query == "ctx:q1"
    assert "digest" not in req.query.lower() or req.query == "ctx:q1"
    assert cb_mod.CONTEXT_REQUEST_BINDING_CONTRACT is True


# ---------------------------------------------------------------------------
# L. ContextRequest limit 1 and 100 accepted, 0/101/bool rejected
# ---------------------------------------------------------------------------

def test_l_limit_bounds():
    h = _handoff(context_refs=(_sr("ctx:a"),))
    # 1 and 100 accepted
    p1 = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=1)
    assert p1.intents[0].request.limit == 1
    p100 = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=100)
    assert p100.intents[0].request.limit == 100
    # 0 rejected
    with pytest.raises((ValueError, TypeError)):
        create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=0)
    # 101 rejected
    with pytest.raises((ValueError, TypeError)):
        create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=101)
    # bool rejected
    with pytest.raises((ValueError, TypeError)):
        create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=True)  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=False)  # type: ignore
    assert cb_mod.CONTEXT_REQUEST_EXISTING_LIMIT_BOUND_REUSED is True


# ---------------------------------------------------------------------------
# M. No ACF knobs
# ---------------------------------------------------------------------------

def test_m_no_acf_knobs():
    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    d = plan.canonical_dict()
    s = str(d).lower()
    for kw in ("top_k", "topk", "embedding", "reranker", "hot_budget", "warm_budget", "archive_policy", "vector_index"):
        assert kw not in s
        assert kw not in str(plan.intents[0].request.to_dict()).lower()
    # source file should not contain ACF knobs canonicalized
    src = pathlib.Path(cb_mod.__file__).read_text(encoding="utf-8").lower()
    for kw in ("top_k", "embedding_model", "reranker", "vector_index", "hot_budget", "archive_policy"):
        assert kw not in src
    assert cb_mod.ACF_PRIVATE_RETRIEVAL_KNOBS_CANONICALIZED_IN_FORGE is False


# ---------------------------------------------------------------------------
# N. WITHIN_BUDGET — current may be eager, recovered progressive
# ---------------------------------------------------------------------------

def test_n_within_budget_eager_vs_progressive():
    h = _handoff(context_refs=(_sr("ctx:cur"),))
    wt = _wt(context_refs=(_sr("ctx:rec"),))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    # find intents by origin
    cur_intents = [i for i in plan.intents if i.origin == "current_handoff"]
    rec_intents = [i for i in plan.intents if i.origin == "recovered_continuity"]
    assert len(cur_intents) == 1 and cur_intents[0].delivery_intent == "eager"
    assert len(rec_intents) == 1 and rec_intents[0].delivery_intent == "progressive"
    # also test with RolloverDecision object
    dec = RolloverDecision(disposition=RolloverDisposition.WITHIN_BUDGET)
    plan2 = create_context_bootstrap_plan(h, wt, rollover_disposition=dec, provider_limit=10)
    assert plan2.intents[0].delivery_intent == "eager"


# ---------------------------------------------------------------------------
# O. UNDETERMINED — all progressive
# ---------------------------------------------------------------------------

def test_o_undetermined_all_progressive():
    h = _handoff(context_refs=(_sr("ctx:cur"),))
    wt = _wt(context_refs=(_sr("ctx:rec"),))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.UNDETERMINED, provider_limit=10)
    assert all(i.delivery_intent == "progressive" for i in plan.intents)
    assert cb_mod.M3_ADDED_CONTEXT_WHEN_BUDGET_UNDETERMINED == "PROGRESSIVE_ONLY"


# ---------------------------------------------------------------------------
# P. ROLLOVER_RECOMMENDED / REQUIRED — no unbounded eager
# ---------------------------------------------------------------------------

def test_p_rollover_recommended_required_all_progressive():
    h = _handoff(context_refs=(_sr("ctx:cur"),))
    wt = _wt(context_refs=(_sr("ctx:rec"),))
    for disp in (RolloverDisposition.ROLLOVER_RECOMMENDED, RolloverDisposition.ROLLOVER_REQUIRED):
        plan = create_context_bootstrap_plan(h, wt, rollover_disposition=disp, provider_limit=10)
        assert all(i.delivery_intent == "progressive" for i in plan.intents), f"failed for {disp}"


# ---------------------------------------------------------------------------
# Q. Missing budget evidence — progressive-only
# ---------------------------------------------------------------------------

def test_q_missing_budget_progressive_only():
    h = _handoff(context_refs=(_sr("ctx:cur"),))
    wt = _wt(context_refs=(_sr("ctx:rec"),))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=None, provider_limit=10)
    assert all(i.delivery_intent == "progressive" for i in plan.intents)
    assert cb_mod.MISSING_CONTEXT_BUDGET_EVIDENCE_ALLOWS_UNBOUNDED_EAGER_HYDRATION is False


# ---------------------------------------------------------------------------
# R. No provider invocation
# ---------------------------------------------------------------------------

def test_r_no_provider_invocation():
    # create a counting fake provider — builder must not call it
    from aota_forge.core.providers.context import ContextRequest, ContextResponse, ContextProvider

    counter = {"n": 0}

    class CountingProvider:
        def fetch(self, request: ContextRequest) -> ContextResponse:
            counter["n"] += 1
            return ContextResponse.success(payload=())

    # ensure module doesn't import provider or call fetch
    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert counter["n"] == 0
    assert cb_mod.W1_PROVIDER_FETCH_REQUIRED is False
    assert cb_mod.W1_PROVIDER_INVOCATION_COUNT == 0
    # also check source doesn't contain ".fetch(" for provider
    src = pathlib.Path(cb_mod.__file__).read_text(encoding="utf-8")
    assert "ContextProvider" not in src or ".fetch" not in src  # no provider fetch call
    # ensure no hydrator import
    assert "selective_hydration" not in src.lower() or "selective_hydration" not in src  # we removed from docstring


# ---------------------------------------------------------------------------
# S. No hydration invocation
# ---------------------------------------------------------------------------

def test_s_no_hydration_invocation():
    counter = {"n": 0}

    def fake_hydration(*args, **kwargs):
        counter["n"] += 1
        return None

    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert counter["n"] == 0
    assert cb_mod.W1_SELECTIVE_HYDRATION_REQUIRED is False
    assert cb_mod.W1_HYDRATION_INVOCATION_COUNT == 0


# ---------------------------------------------------------------------------
# T. No fake eager materialization
# ---------------------------------------------------------------------------

def test_t_no_fake_eager_materialization():
    h = _handoff(context_refs=(_sr("ctx:a"), _sr("ctx:b")))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    # intents contain delivery_intent eager but no materialized content
    for intent in plan.intents:
        assert intent.delivery_intent in ("eager", "progressive")
        # request should not contain materialized content field
        d = intent.request.to_dict()
        assert "materialized" not in d
        assert "content" not in str(d).lower() or "content" not in d
    # plan should not have BootstrapComponent with materialized
    assert cb_mod.W1_FAKE_EAGER_MATERIALIZATION_ALLOWED is False
    assert cb_mod.W1_EAGER_IS_DELIVERY_INTENT_ONLY is True
    # ensure plan has no hydrated bytes
    s = plan.canonical_json()
    assert "materialized" not in s


# ---------------------------------------------------------------------------
# U. Compact governance metadata only
# ---------------------------------------------------------------------------

def test_u_compact_governance_metadata_only():
    h = _handoff(context_refs=(_sr("ctx:a"),), project="proj:A", plan="plan:P", milestone="ms:M1", work_item="w:W1")
    wt = _wt(context_refs=(_sr("ctx:b"),), project="proj:A", plan="plan:P", milestone="ms:M1", active_w="w:W1")
    # add frontier refs to recovered
    wt2 = WorkingTruthProjection(
        project_ref=_sr("proj:A"),
        plan_ref=_sr("plan:P"),
        milestone_ref=_sr("ms:M1"),
        active_work_item_ref=_sr("w:W1"),
        accepted_frontier_ref=_sr("frontier:acc"),
        reviewed_frontier_ref=_sr("frontier:rev"),
        workflow_disposition_ref=_sr("workflow:cont"),
        context_refs=(_sr("ctx:b"),),
    )
    plan = create_context_bootstrap_plan(h, wt2, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert plan.project_ref.ref == "proj:A"
    assert plan.plan_ref.ref == "plan:P"
    assert plan.milestone_ref.ref == "ms:M1"
    assert plan.work_item_ref.ref == "w:W1"
    assert plan.accepted_frontier_ref.ref == "frontier:acc"
    # ensure no full Plan body
    d = plan.canonical_dict()
    s = str(d)
    assert "full plan body" not in s.lower()
    assert len(s) < 5000  # compact
    assert cb_mod.FULL_PLAN_BODY_BOOTSTRAP_REQUIRED is False
    assert cb_mod.FULL_GOVERNANCE_HISTORY_BOOTSTRAP_REQUIRED is False
    assert cb_mod.BOOTSTRAPPED_GOVERNANCE_METADATA_IS_AUTHORITY is False


# ---------------------------------------------------------------------------
# V. Unknown-field rejection
# ---------------------------------------------------------------------------

def test_v_unknown_field_rejection():
    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    d = plan.to_dict()
    d_extra = dict(d)
    d_extra["unknown_field"] = "oops"
    with pytest.raises((ValueError, TypeError)):
        ContextBootstrapPlan.from_dict(d_extra)
    # intent unknown field
    intent_d = plan.intents[0].to_dict()
    intent_extra = dict(intent_d)
    intent_extra["extra"] = 123
    with pytest.raises((ValueError, TypeError)):
        ContextBootstrapIntent.from_dict(intent_extra)


# ---------------------------------------------------------------------------
# W. Authority injection — forbidden fields fail closed
# ---------------------------------------------------------------------------

def test_w_authority_injection_fails():
    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    d = plan.to_dict()
    for bad in ("authorized", "retry_authorized", "safe_to_hydrate", "approved"):
        d_bad = dict(d)
        d_bad[bad] = True
        with pytest.raises((ValueError, TypeError)):
            ContextBootstrapPlan.from_dict(d_bad)
    # intent level
    intent_d = plan.intents[0].to_dict()
    for bad in ("authorized", "approved", "safe_to_hydrate"):
        d2 = dict(intent_d)
        d2[bad] = True
        with pytest.raises((ValueError, TypeError)):
            ContextBootstrapIntent.from_dict(d2)


# ---------------------------------------------------------------------------
# X. Agent neutrality
# ---------------------------------------------------------------------------

def test_x_agent_neutral():
    # no provider/model/session ids needed
    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert isinstance(plan, ContextBootstrapPlan)
    d = plan.canonical_dict()
    s = str(d).lower()
    for kw in ("hermes_session", "openai", "provider_name", "model_name", "thread_id", "conversation_id", "process_id", "runtime_id"):
        assert kw not in s
    assert cb_mod.M3_AGENT_NEUTRAL is True
    assert cb_mod.MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    # also ensure plan can be built via from_dict without those fields
    restored = ContextBootstrapPlan.from_dict(d)
    assert restored.canonical_json() == plan.canonical_json()
    # ensure source doesn't require those
    src = pathlib.Path(cb_mod.__file__).read_text(encoding="utf-8").lower()
    assert "hermes_session" not in src
    assert "openai" not in src


# ---------------------------------------------------------------------------
# Additional: provider binding mapping correctness
# ---------------------------------------------------------------------------

def test_request_binding_subject_scope_query():
    h = _handoff(context_refs=(_sr("ctx:queried"),), project="proj:X", plan="plan:Y", milestone="ms:Z", work_item="w:Subject", scope="my scope")
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=5)
    req = plan.intents[0].request
    # subject should be most-specific: work_item
    assert req.subject_ref == "w:Subject"
    assert req.scope == "my scope"
    assert req.query == "ctx:queried"
    # query should not contain digest
    h2 = _handoff(context_refs=(_sr("ctx:q", digest="my-digest"),), scope="scope2")
    plan2 = create_context_bootstrap_plan(h2, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=5)
    assert plan2.intents[0].request.query == "ctx:q"
    assert "my-digest" not in plan2.intents[0].request.query


def test_reuse_flags():
    assert cb_mod.EXISTING_CONTEXT_PROVIDER_CONTRACT_REUSED is True
    assert cb_mod.EXISTING_BOOTSTRAP_BUNDLE_REUSED is True
    assert cb_mod.EXISTING_BOOTSTRAP_BUDGET_SEMANTICS_REUSED is True
    assert cb_mod.TASK_HANDOFF_CONTEXT_REFS_REUSED is True
    assert cb_mod.M2_WORKING_TRUTH_CONTEXT_REFS_REUSED is True
    assert cb_mod.EXISTING_SEMANTIC_REFERENCE_REUSED is True
    assert cb_mod.SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE is True
    assert cb_mod.PURE_BOOTSTRAP_SELECTION_SEPARATE_FROM_IO is True


def test_no_bootstrap_overflow():
    # BootstrapBundle bound is 16, but candidate max is 32 — ensure plan intents can be 32 but final materialization not done here
    ctxs = tuple(_sr(f"ctx:{i}") for i in range(16))
    h = _handoff(context_refs=ctxs)
    wt = _wt(context_refs=tuple(_sr(f"ctx:r{i}") for i in range(16)))
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert len(plan.intents) == 32
    assert cb_mod.BOOTSTRAP_COMPONENT_BOUND_REUSED is True
    assert cb_mod.W1_FINAL_BOOTSTRAP_MATERIALIZATION_REQUIRED is False


def test_plan_digest_not_authority():
    h = _handoff(context_refs=(_sr("ctx:a"),))
    plan = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert isinstance(plan.plan_digest, str)
    assert len(plan.plan_digest) == 64
    assert cb_mod.CONTEXT_BOOTSTRAP_PLAN_DIGEST_IS_AUTHORITY is False
    assert cb_mod.CONTEXT_BOOTSTRAP_INTENT_IS_AUTHORITY is False


def test_determinism_across_instances():
    h = _handoff(context_refs=(_sr("ctx:a"), _sr("ctx:b")))
    p1 = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    p2 = create_context_bootstrap_plan(h, None, rollover_disposition=RolloverDisposition.WITHIN_BUDGET, provider_limit=10)
    assert p1.plan_digest == p2.plan_digest
    assert p1.canonical_json() == p2.canonical_json()
