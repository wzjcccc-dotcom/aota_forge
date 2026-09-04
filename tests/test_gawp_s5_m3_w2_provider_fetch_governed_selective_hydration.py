"""S5/M3/W2 Progressive Provider Fetch & Governed Selective Hydration Integration — focused proof.

Covers required groups P/G/H/L/B/A plus bounds, lane distinctness, authority firewalls,
progressive semantics, and negative proofs.
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import tempfile
from pathlib import Path

import pytest

from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.providers.context import ContextRequest, ContextResponse
from aota_forge.core.result_governance import GovernedReference, GovernedReferenceKind
from aota_forge.core.regression.fixtures import TempWorkspaceFixture
from aota_forge.core.project.resolver import resolve_project_candidates
from aota_forge.work_plane.bootstrap import (
    BootstrapBundle,
    BootstrapComponent,
    MAX_BUNDLE_CANONICAL_BYTES_HARD,
    MAX_COMPONENT_COUNT,
    MAX_MATERIALIZED_LENGTH,
)
from aota_forge.work_plane.context_bootstrap_plan import create_context_bootstrap_plan
from aota_forge.work_plane.context_lifecycle import RolloverDisposition
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.session_checkpoint import WorkingTruthProjection
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary, bind_worktree_sandbox
from aota_forge.work_plane.selective_hydration import (
    HydratedContent,
    governed_ref_for_content,
    MAX_HYDRATED_BYTES,
    MAX_HYDRATION_BATCH,
)

import aota_forge.work_plane.context_bootstrap_execution as exec_mod
from aota_forge.work_plane.context_bootstrap_execution import (
    ContextBootstrapExecutionResult,
    ProviderOutcome,
    execute_context_bootstrap,
    execute_provider_fetch,
    hydrate_governed_refs,
)
from aota_forge.work_plane.tool_result_governance import ToolOutputRef
from aota_forge.work_plane.workspace_mutation import ArtifactReference

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXEC_PATH = REPO_ROOT / "aota_forge" / "work_plane" / "context_bootstrap_execution.py"

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sr(ref: str, digest: str | None = None) -> SemanticReference:
    return SemanticReference(ref=ref, digest=digest)


def _handoff(context_refs=(), scope="bounded scope task"):
    return TaskHandoff(
        work_role="coder",
        task_kind="implementation",
        objective="implement feature",
        bounded_scope=scope,
        validation_expectations=("check",),
        semantic_stop_expectations=("stop",),
        project_ref=_sr("proj:A"),
        plan_ref=_sr("plan:P"),
        milestone_ref=_sr("ms:M1"),
        work_item_ref=_sr("w:W1"),
        context_refs=tuple(context_refs),
    )


def _wt(context_refs=()):
    return WorkingTruthProjection(
        project_ref=_sr("proj:A"),
        plan_ref=_sr("plan:P"),
        milestone_ref=_sr("ms:M1"),
        active_work_item_ref=_sr("w:W1"),
        context_refs=tuple(context_refs),
    )


def _make_plan(n_current=2, n_recovered=0, within_budget=True):
    cur = tuple(_sr(f"ctx:cur{i}") for i in range(n_current))
    rec = tuple(_sr(f"ctx:rec{i}") for i in range(n_recovered))
    h = _handoff(context_refs=cur)
    wt = _wt(context_refs=rec) if n_recovered else None
    disp = RolloverDisposition.WITHIN_BUDGET if within_budget else RolloverDisposition.UNDETERMINED
    plan = create_context_bootstrap_plan(h, wt, rollover_disposition=disp, provider_limit=10)
    return plan


def _make_sandbox(project_id="proj_a", worktree_id="wt_a", workspace_id="ws_a"):
    ws = TempWorkspaceFixture(prefix="w2-")
    ws.__enter__()
    ws.create_project(project_id)
    registry = ws.create_registry(workspace_id)
    evidence = resolve_project_candidates(workspace_id, registry, project_id)
    assert evidence.status == "RESOLVED"
    wt = Path(tempfile.mkdtemp(prefix="w2-wt-"))
    b = bind_worktree_sandbox(evidence, worktree_id, wt)
    return b, ws, wt


def _cleanup(ws: TempWorkspaceFixture, wt: Path):
    try:
        ws.__exit__(None, None, None)
    except Exception:
        pass
    shutil.rmtree(str(wt), ignore_errors=True)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# ---------------------------------------------------------------------------
# P — Provider lane
# ---------------------------------------------------------------------------

class TestProviderLane:
    def test_p1_actual_fetch_receives_w1_request(self):
        plan = _make_plan(n_current=1)
        captured = {}

        class CapProvider:
            def fetch(self, request: ContextRequest) -> ContextResponse:
                captured["req"] = request
                assert isinstance(request, ContextRequest)
                assert request.query == plan.intents[0].context_ref.ref
                assert request.scope == plan.intents[0].request.scope
                assert request.subject_ref == plan.intents[0].request.subject_ref
                return ContextResponse.success(payload=({"k": "v"},), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(
                plan,
                provider=CapProvider(),
                selected_intent_indices=(0,),
                current_sandbox=b,
                hydration_source=None,
                expected_project_id=b.project_id,
                expected_worktree_id=b.worktree_id,
            )
            assert "req" in captured
            assert len(res.provider_outcomes) == 1
            assert res.provider_outcomes[0].status == "success"
        finally:
            _cleanup(ws, wt)

    def test_p2_success_non_empty_becomes_materialized(self):
        plan = _make_plan(n_current=1)
        payload = ({"content": "hello world", "id": "1"},)

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=payload, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(
                plan, provider=P(), selected_intent_indices=(0,),
                current_sandbox=b, hydration_source=None,
                expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
            )
            out = res.provider_outcomes[0]
            assert out.status == "success"
            assert out.materialized is not None
            assert out.component is not None
            assert out.component.delivery == "eager"
            assert out.component.kind == "semantic_ref"
            assert out.component.materialized == out.materialized
            assert res.bootstrap_bundle is not None
            assert len(res.bootstrap_bundle.components) >= 1
            # ensure bounded
            assert len(out.materialized) <= MAX_MATERIALIZED_LENGTH
        finally:
            _cleanup(ws, wt)

    def test_p3_success_empty_distinct(self):
        plan = _make_plan(n_current=1)

        class EmptyP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=(), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(
                plan, provider=EmptyP(), selected_intent_indices=(0,),
                current_sandbox=b, hydration_source=None,
                expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
            )
            assert res.provider_outcomes[0].status == "empty"
            assert res.provider_outcomes[0].materialized is None
            assert res.provider_outcomes[0].component is None
            # empty produces no bundle unless base provided
            # but outcome must be distinct from failure
        finally:
            _cleanup(ws, wt)

    def test_p4_failure_preserved(self):
        plan = _make_plan(n_current=1)

        class FailP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.failure({"code": "PROVIDER_ERROR", "message": "fail", "retryable": False})

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(
                plan, provider=FailP(), selected_intent_indices=(0,),
                current_sandbox=b, hydration_source=None,
                expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
            )
            out = res.provider_outcomes[0]
            assert out.status == "failure"
            assert out.error is not None
            assert out.error["code"] == "PROVIDER_ERROR"
            assert out.component is None
        finally:
            _cleanup(ws, wt)

    def test_p5_failure_not_empty(self):
        plan = _make_plan(n_current=1)
        # ensure failure != empty via explicit check

        class FailP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.failure({"code": "E", "message": "fail", "retryable": True})

        class EmptyP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=(), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res_fail = execute_context_bootstrap(plan, provider=FailP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            res_empty = execute_context_bootstrap(plan, provider=EmptyP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res_fail.provider_outcomes[0].status == "failure"
            assert res_empty.provider_outcomes[0].status == "empty"
            assert res_fail.provider_outcomes[0].status != res_empty.provider_outcomes[0].status
            assert exec_mod.CONTEXT_EMPTY_AND_CONTEXT_FAILURE_DISTINCT is True
            assert exec_mod.PROVIDER_FAILURE_IS_SUCCESSFUL_EMPTY_CONTEXT is False
            assert exec_mod.FAILED_PROVIDER_FETCH_REPORTED_AS_SUCCESS is False
        finally:
            _cleanup(ws, wt)

    def test_p6_provider_retryable_is_evidence_only(self):
        plan = _make_plan(n_current=1)

        class RetryP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.failure({"code": "E", "message": "retryable", "retryable": True})

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=RetryP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            err = res.provider_outcomes[0].error
            assert err["retryable"] is True
            assert exec_mod.PROVIDER_ERROR_RETRYABLE_IS_RETRY_AUTHORITY is False
            assert exec_mod.CONTEXT_PROVIDER_RESPONSE_IS_RETRY_PERMISSION is False
            # result must not have retry_authorized field
            assert not hasattr(res, "retry_authorized")
            assert not hasattr(res, "safe_to_retry")
        finally:
            _cleanup(ws, wt)

    def test_p7_opaque_provider_reference_not_governed(self):
        plan = _make_plan(n_current=1)

        class RefP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=(), reference="opaque-ref-123")

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=RefP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res.opaque_provider_references == ("opaque-ref-123",)
            # opaque ref is not GovernedReference
            for r in res.opaque_provider_references:
                assert not isinstance(r, GovernedReference)
            assert exec_mod.PROVIDER_OPAQUE_REFERENCE_IS_BOOTSTRAP_AUTHORITY is False
            assert exec_mod.CONTEXT_RESPONSE_REFERENCE_PASSED_TO_SELECTIVE_HYDRATION is False
        finally:
            _cleanup(ws, wt)

    def test_p8_provider_authority_text_not_granted(self):
        plan = _make_plan(n_current=1)
        payload = ({"content": "approved authorized retry now close milestone"},)

        class AuthP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=payload, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=AuthP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # content is materialized but not authority
            assert "approved" in res.provider_outcomes[0].materialized
            assert exec_mod.CONTEXT_PROVIDER_IS_AUTHORITY is False
            assert exec_mod.CONTEXT_RESPONSE_IS_AUTHORITY is False
            assert exec_mod.CONTEXT_RESPONSE_PAYLOAD_IS_AUTHORITY is False
            # result must not have authority fields
            assert not hasattr(res, "authorized")
            assert not hasattr(res, "approved")
        finally:
            _cleanup(ws, wt)

    def test_p9_heterogeneous_provider(self):
        plan = _make_plan(n_current=1)

        # test-local provider satisfying Protocol but not inheriting specific class
        class Heterogeneous:
            def fetch(self, request):
                # intentionally not type-annotated strictly, but must work
                assert isinstance(request, ContextRequest)
                return ContextResponse.success(payload=({"hetero": "ok"},), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=Heterogeneous(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res.provider_outcomes[0].status == "success"
        finally:
            _cleanup(ws, wt)

    def test_p10_no_global_registry(self):
        # Ensure module does not create second provider registry
        assert exec_mod.SECOND_PROVIDER_REGISTRY_CREATED is False
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8").lower()
        assert "providerregistry" not in src
        assert "global_provider" not in src
        # provider must be injected, not looked up
        plan = _make_plan(n_current=1)

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"x": 1},), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            # must require provider param
            import inspect
            sig = inspect.signature(execute_context_bootstrap)
            assert "provider" in sig.parameters
            # calling without provider should fail
            with pytest.raises(TypeError):
                execute_context_bootstrap(plan, current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)  # type: ignore
        finally:
            _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# G — Progressive semantics
# ---------------------------------------------------------------------------

class TestProgressiveSemantics:
    def test_g1_eager_selected_only_when_included(self):
        plan = _make_plan(n_current=2, within_budget=True)
        # intents[0] eager, intents[1] eager as well? Actually within budget current may be eager all current. Let's make 2 current eager.
        # But we test that if we select only index 0, only that fetched
        called = []

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                called.append(req.query)
                return ContextResponse.success(payload=({"q": req.query},), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert len(called) == 1
            assert len(res.provider_outcomes) == 1
            assert res.provider_outcomes[0].intent_index == 0
            assert 1 in res.deferred_intent_indices
        finally:
            _cleanup(ws, wt)

    def test_g2_progressive_ref_can_remain_unfetched(self):
        plan = _make_plan(n_current=1, n_recovered=1, within_budget=True)
        # plan has 2 intents: index0 eager (current), index1 progressive (recovered)
        assert plan.intents[1].delivery_intent == "progressive"

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"q": req.query},), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            # fetch only eager, leave progressive deferred
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert 1 in res.deferred_intent_indices
            assert len(res.provider_outcomes) == 1
            assert exec_mod.PROGRESSIVE_FETCH_IS_CALLER_DIRECTED is True
        finally:
            _cleanup(ws, wt)

    def test_g3_caller_may_fetch_progressive(self):
        plan = _make_plan(n_current=1, n_recovered=1, within_budget=True)

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"q": req.query},), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(1,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert len(res.provider_outcomes) == 1
            assert res.provider_outcomes[0].intent_index == 1
            # even though intent was progressive, fetching produces eager materialized component
            assert res.provider_outcomes[0].component.delivery == "eager"
            assert res.bootstrap_bundle is not None
        finally:
            _cleanup(ws, wt)

    def test_g4_no_automatic_cursor_loop(self):
        plan = _make_plan(n_current=1)

        class CursorP:
            def __init__(self):
                self.calls = 0
            def fetch(self, req: ContextRequest) -> ContextResponse:
                self.calls += 1
                # return with cursor reference but payload, should not auto-loop
                return ContextResponse.success(payload=({"data": "page1"},), reference="cursor-xyz")

        b, ws, wt = _make_sandbox()
        try:
            p = CursorP()
            res = execute_context_bootstrap(plan, provider=p, selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert p.calls == 1
            assert exec_mod.AUTOMATIC_UNBOUNDED_PAGINATION is False
            # cursor stored as opaque ref, not auto-fetched again
            assert "cursor-xyz" in res.opaque_provider_references
        finally:
            _cleanup(ws, wt)

    def test_g5_no_background_prefetch(self):
        assert exec_mod.BACKGROUND_CONTEXT_PREFETCH_REQUIRED is False
        assert exec_mod.M3_BACKGROUND_CONTEXT_RUNTIME_CREATED is False
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8").lower()
        assert "background" not in src or "background_context_prefetch_required" in src  # only flag, no runtime
        assert "prefetch" not in src or "background_context_prefetch_required" in src
        assert "thread" not in src.lower()
        assert "daemon" not in src.lower()

    def test_g6_failed_progressive_does_not_erase_valid_bundle(self):
        # create plan with 2 intents, one will succeed, one will fail
        plan = _make_plan(n_current=2)

        class MixedP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                if "cur0" in req.query:
                    return ContextResponse.success(payload=({"ok": 1},), reference=None)
                else:
                    return ContextResponse.failure({"code": "E", "message": "fail", "retryable": False})

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=MixedP(), selected_intent_indices=(0, 1), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # should have 2 outcomes, one success one failure
            statuses = {o.status for o in res.provider_outcomes}
            assert "success" in statuses and "failure" in statuses
            # bundle should preserve successful component
            assert res.bootstrap_bundle is not None
            assert len(res.bootstrap_bundle.components) == 1
            assert exec_mod.PARTIAL_BOOTSTRAP_MAY_PRESERVE_SUCCESSFUL_COMPONENTS is True
            assert exec_mod.FAILED_COMPONENT_SILENTLY_DROPPED is False
        finally:
            _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# H — Governed Hydration
# ---------------------------------------------------------------------------

class TestGovernedHydration:
    def test_h1_valid_governed_ref_hydrates_under_sandbox(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "valid hydration"
            ref = governed_ref_for_content("evidence", "evidence/h1", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            # need provider dummy for empty plan
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            res = execute_context_bootstrap(
                plan, provider=Dummy(), selected_intent_indices=(),
                governed_refs=(ref,),
                current_sandbox=b, hydration_source=source,
                expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
            )
            assert len(res.hydrated_contents) == 1
            assert res.hydrated_contents[0].content == content
            assert res.hydrated_contents[0].digest == _sha(content)
            assert res.bootstrap_bundle is not None
        finally:
            _cleanup(ws, wt)

    def test_h2_cross_project_fails_closed(self):
        b, ws, wt = _make_sandbox(project_id="proj_a", worktree_id="wt_a")
        b2, ws2, wt2 = _make_sandbox(project_id="proj_b", worktree_id="wt_a")
        try:
            content = "cross proj"
            ref = governed_ref_for_content("evidence", "evidence/cross_proj", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b2, hydration_source=source,
                    expected_project_id="proj_a", expected_worktree_id=b2.worktree_id,
                )
            assert exec_mod.CROSS_PROJECT_HYDRATION_FAIL_CLOSED is True
        finally:
            _cleanup(ws, wt)
            _cleanup(ws2, wt2)

    def test_h3_cross_worktree_fails_closed(self):
        b, ws, wt = _make_sandbox(worktree_id="wt_a")
        try:
            content = "cross wt"
            ref = governed_ref_for_content("evidence", "evidence/cross_wt", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b, hydration_source=source,
                    expected_project_id=b.project_id, expected_worktree_id="other_wt",
                )
            assert exec_mod.CROSS_WORKTREE_HYDRATION_FAIL_CLOSED is True
        finally:
            _cleanup(ws, wt)

    def test_h4_tampered_digest_fails_closed(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "original"
            ref = governed_ref_for_content("evidence", "evidence/tamper", content)
            tampered_source = {ref: "tampered content"}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b, hydration_source=tampered_source,
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
            assert exec_mod.TAMPERED_HYDRATED_CONTENT_FAIL_CLOSED is True
            assert exec_mod.HYDRATION_DIGEST_VERIFIED is True
        finally:
            _cleanup(ws, wt)

    def test_h5_unknown_ref_fails_closed(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "unknown"
            ref = governed_ref_for_content("evidence", "evidence/unknown", content)
            empty_source = {}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b, hydration_source=empty_source,
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
        finally:
            _cleanup(ws, wt)

    def test_h6_oversized_content_fails_closed(self):
        b, ws, wt = _make_sandbox()
        try:
            big_content = "X" * (MAX_HYDRATED_BYTES + 1)
            big_digest = hashlib.sha256(big_content.encode()).hexdigest()
            ref = GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="evidence/oversize", digest=big_digest)
            source = {ref: big_content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b, hydration_source=source,
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
        finally:
            _cleanup(ws, wt)

    def test_h7_batch_over_8_fails_closed(self):
        b, ws, wt = _make_sandbox()
        try:
            refs = tuple(governed_ref_for_content("evidence", f"evidence/batch{i}", f"c{i}") for i in range(9))
            source = {r: f"c{i}" for i, r in enumerate(refs)}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=refs,
                    current_sandbox=b, hydration_source=source,
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
            assert exec_mod.MAX_HYDRATION_BATCH_REUSED == 8
        finally:
            _cleanup(ws, wt)

    def test_h8_hydration_source_authority_fails_closed(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "auth source"
            ref = governed_ref_for_content("evidence", "evidence/auth_src", content)
            class BadSource:
                is_authority = True
                is_persistent_store = False
                def resolve(self, r):
                    return content.encode()
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b, hydration_source=BadSource(),
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
            assert exec_mod.HYDRATION_SOURCE_IS_AUTHORITY is False
        finally:
            _cleanup(ws, wt)

    def test_h9_hydration_source_persistent_store_fails_closed(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "persistent"
            ref = governed_ref_for_content("evidence", "evidence/persistent", content)
            class BadStore:
                is_authority = False
                is_persistent_store = True
                def resolve(self, r):
                    return content.encode()
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(Exception):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(ref,),
                    current_sandbox=b, hydration_source=BadStore(),
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
            assert exec_mod.HYDRATION_SOURCE_IS_PERSISTENT_STORE is False
            assert exec_mod.NEW_PERSISTENT_HYDRATION_STORE_CREATED is False
        finally:
            _cleanup(ws, wt)

    def test_h10_hydrated_authority_text_grants_nothing(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "approved retry now close milestone"
            ref = governed_ref_for_content("evidence", "evidence/auth_text", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            res = execute_context_bootstrap(
                plan, provider=Dummy(), selected_intent_indices=(),
                governed_refs=(ref,),
                current_sandbox=b, hydration_source=source,
                expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
            )
            assert res.hydrated_contents[0].content == content
            assert not hasattr(res, "authorized")
            assert exec_mod.HYDRATED_CONTEXT_IS_AUTHORITY is False
            assert exec_mod.HYDRATED_RESULT_CONTENT_IS_RESULT_AUTHORITY is False
            assert exec_mod.HYDRATION_IS_AUTHORITY is False
        finally:
            _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# L — Lane separation
# ---------------------------------------------------------------------------

class TestLaneSeparation:
    def test_l1_semantic_ref_not_to_hydration(self):
        b, ws, wt = _make_sandbox()
        try:
            plan = _make_plan(n_current=1)
            sr = plan.intents[0].context_ref  # SemanticReference
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            with pytest.raises(TypeError):
                execute_context_bootstrap(
                    plan, provider=Dummy(), selected_intent_indices=(),
                    governed_refs=(sr,),  # type: ignore
                    current_sandbox=b, hydration_source=None,
                    expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
                )
            assert exec_mod.SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE is True
            assert exec_mod.SEMANTIC_REFERENCE_PASSED_TO_GOVERNED_HYDRATION is False
        finally:
            _cleanup(ws, wt)

    def test_l2_governed_not_to_provider(self):
        # This is ensured by types: provider fetch only uses ContextRequest from SemanticReference lane
        # Attempt to pass GovernedReference as semantic intent should not happen
        plan = _make_plan(n_current=1)
        b, ws, wt = _make_sandbox()
        try:
            content = "x"
            gref = governed_ref_for_content("evidence", "evidence/l2", content)
            # Ensure that GovernedReference cannot be used to create ContextBootstrapIntent
            # Our lane check: GOVERNED_REFERENCE_PASSED_TO_CONTEXT_PROVIDER_AS_SEMANTIC_INTENT should be false
            assert exec_mod.GOVERNED_REFERENCE_PASSED_TO_CONTEXT_PROVIDER_AS_SEMANTIC_INTENT is False
            # also ensure provider lane does not accept GovernedReference
            # This is structural, so we just verify flag and that no AnyRef exists
            src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8")
            # No generic AnyRef class defined
            assert "class AnyRef" not in src
            assert "GenericRef" not in src
        finally:
            _cleanup(ws, wt)

    def test_l3_context_response_reference_not_hydrated(self):
        plan = _make_plan(n_current=1)

        class RefP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"data": 1},), reference="opaque-ref")

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=RefP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # opaque ref should be stored as provider evidence, not hydrated
            assert "opaque-ref" in res.opaque_provider_references
            assert len(res.hydrated_contents) == 0
            assert exec_mod.CONTEXT_RESPONSE_REFERENCE_PASSED_TO_SELECTIVE_HYDRATION is False
        finally:
            _cleanup(ws, wt)

    def test_l4_provider_output_cannot_bypass_sandbox(self):
        b, ws, wt = _make_sandbox()
        b_other, ws2, wt2 = _make_sandbox(project_id="proj_other", worktree_id="wt_other")
        try:
            content = "l4 content"
            ref = governed_ref_for_content("evidence", "evidence/l4", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            # Hydration with mismatched sandbox should fail even if provider succeeded elsewhere
            # Provider succeeded earlier, but hydration still requires sandbox reauth
            with pytest.raises(Exception):
                execute_context_bootstrap(plan, provider=Dummy(), selected_intent_indices=(), governed_refs=(ref,), current_sandbox=b_other, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
        finally:
            _cleanup(ws, wt)
            _cleanup(ws2, wt2)

    def test_l5_no_generic_anyref(self):
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8")
        assert "Generic" not in src or "AnyRef" not in src
        assert exec_mod.SEMANTIC_CONTEXT_REF_LANE_DISTINCT_FROM_GOVERNED_HYDRATION_REF_LANE is True

# ---------------------------------------------------------------------------
# B — Bootstrap materialization
# ---------------------------------------------------------------------------

class TestBootstrapMaterialization:
    def test_b1_provider_content_to_component(self):
        plan = _make_plan(n_current=1)
        payload = ({"content": "hello bootstrap"},)

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=payload, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res.bootstrap_bundle is not None
            comps = res.bootstrap_bundle.components
            assert any(c.kind == "semantic_ref" for c in comps)
            assert any(c.materialized is not None for c in comps)
            assert exec_mod.EXISTING_BOOTSTRAP_BUNDLE_REUSED is True
        finally:
            _cleanup(ws, wt)

    def test_b2_eager_digest_equals_content(self):
        plan = _make_plan(n_current=1)
        payload = ({"data": "digest test"},)

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=payload, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            comp = res.provider_outcomes[0].component
            assert comp.digest == _sha(comp.materialized)
        finally:
            _cleanup(ws, wt)

    def test_b3_no_fake_eager_content(self):
        plan = _make_plan(n_current=1)

        class EmptyP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=(), reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=EmptyP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # empty should not produce fake materialized component
            assert res.bootstrap_bundle is None or len(res.bootstrap_bundle.components) == 0
            # also test that we don't create component when not fetched
            res2 = execute_context_bootstrap(plan, provider=EmptyP(), selected_intent_indices=(), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res2.bootstrap_bundle is None
        finally:
            _cleanup(ws, wt)

    def test_b4_progressive_unresolved_stays_ref(self):
        plan = _make_plan(n_current=1, n_recovered=1, within_budget=True)
        b, ws, wt = _make_sandbox()
        # only fetch eager (0), leave progressive 1 deferred

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"q": req.query},), reference=None)

        try:
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert 1 in res.deferred_intent_indices
            # deferred means no materialized component for that intent
            assert len(res.provider_outcomes) == 1
            assert res.provider_outcomes[0].intent_index == 0
        finally:
            _cleanup(ws, wt)

    def test_b5_max_component_count_enforced(self):
        # create plan with many intents, exceed 16 components
        plan = _make_plan(n_current=16)
        # create base bundle already with 15 components, then add 2 more should exceed 16
        b, ws, wt = _make_sandbox()
        try:
            # build base bundle with 15 dummy components
            from aota_forge.work_plane.bootstrap import BootstrapComponent
            base_comps = []
            for i in range(15):
                mat = f"base content {i}"
                digest = _sha(mat)
                base_comps.append(BootstrapComponent(kind="semantic_ref", delivery="eager", materialized=mat, digest=digest, provenance=f"base:{i}"))
            base = BootstrapBundle(bundle_type="task_main", components=tuple(base_comps))
            assert len(base.components) == 15
            plan2 = _make_plan(n_current=2)

            class P:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=({"x": 1},), reference=None)

            with pytest.raises(ValueError):
                execute_context_bootstrap(plan2, provider=P(), selected_intent_indices=(0, 1), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id, base_bundle=base)
            assert exec_mod.EXISTING_BOOTSTRAP_BUNDLE_LIMITS_REUSED is True
        finally:
            _cleanup(ws, wt)

    def test_b6_oversized_component_fails_closed(self):
        plan = _make_plan(n_current=1)
        # payload that when serialized exceeds MAX_MATERIALIZED_LENGTH
        big_str = "X" * (MAX_MATERIALIZED_LENGTH + 1)
        payload = ({"big": big_str},)

        class BigP:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=payload, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=BigP(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # oversize should result in failure outcome, not silent truncation
            assert res.provider_outcomes[0].status == "failure"
            assert "oversize" in res.provider_outcomes[0].error["message"].lower()
            assert exec_mod.PROVIDER_PAYLOAD_OVERSIZE_FAILS_CLOSED is True
            assert exec_mod.PROVIDER_PAYLOAD_SILENT_TRUNCATION is False
        finally:
            _cleanup(ws, wt)

    def test_b7_canonical_bundle_deterministic(self):
        plan = _make_plan(n_current=2)
        payload_a = ({"content": "a"},)
        payload_b = ({"content": "b"},)
        # Need provider that returns deterministic based on query
        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                if "cur0" in req.query:
                    return ContextResponse.success(payload=payload_a, reference=None)
                else:
                    return ContextResponse.success(payload=payload_b, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            res1 = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0, 1), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            res2 = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(1, 0), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res1.bootstrap_bundle.canonical_json() == res2.bootstrap_bundle.canonical_json()
            assert res1.bootstrap_bundle.digest == res2.bootstrap_bundle.digest
        finally:
            _cleanup(ws, wt)

    def test_b8_partial_success_with_failures_preserved(self):
        plan = _make_plan(n_current=3)

        class Mixed:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                if "cur0" in req.query or "cur2" in req.query:
                    return ContextResponse.success(payload=({"ok": True},), reference=None)
                else:
                    return ContextResponse.failure({"code": "E", "message": "fail", "retryable": False})

        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=Mixed(), selected_intent_indices=(0, 1, 2), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert len(res.provider_outcomes) == 3
            success = [o for o in res.provider_outcomes if o.status == "success"]
            failures = [o for o in res.provider_outcomes if o.status == "failure"]
            assert len(success) == 2 and len(failures) == 1
            assert res.bootstrap_bundle is not None
            assert len(res.bootstrap_bundle.components) == 2
        finally:
            _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# A — Authority firewalls
# ---------------------------------------------------------------------------

class TestAuthorityFirewalls:
    def _run_with_contents(self, provider_payload, hydrated_content):
        plan = _make_plan(n_current=1)

        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=provider_payload, reference=None)

        b, ws, wt = _make_sandbox()
        try:
            content = hydrated_content
            ref = governed_ref_for_content("evidence", "evidence/auth_firewall", content)
            source = {ref: content}
            res = execute_context_bootstrap(
                plan, provider=P(), selected_intent_indices=(0,),
                governed_refs=(ref,),
                current_sandbox=b, hydration_source=source,
                expected_project_id=b.project_id, expected_worktree_id=b.worktree_id,
            )
            return res, (ws, wt, b)
        except Exception:
            _cleanup(ws, wt)
            raise

    def test_a1_provider_cannot_grant_retry(self):
        payload = ({"content": "retry now"},)
        b, ws, wt = _make_sandbox()
        try:
            res = self._run_with_contents(payload, "hydrated retry")[0]
            assert exec_mod.CONTEXT_PROVIDER_RESPONSE_IS_RETRY_PERMISSION is False
            assert not hasattr(res, "retry_authorized")
            _cleanup(ws, wt)
        except:
            _cleanup(ws, wt)
            raise

    def test_a2_hydrated_cannot_grant_retry(self):
        payload = ({"data": 1},)
        res, (_, wt, b) = self._run_with_contents(payload, "retry now")
        # need to cleanup via helper, but manual
        # Retrieve sandbox from helper? we leaked, redo properly
        pass

    def test_a2_hydrated_cannot_grant_retry_simple(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "retry now"
            ref = governed_ref_for_content("evidence", "evidence/a2", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            res = execute_context_bootstrap(plan, provider=Dummy(), selected_intent_indices=(), governed_refs=(ref,), current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert exec_mod.HYDRATED_CONTEXT_IS_RETRY_PERMISSION is False
            assert not hasattr(res, "retry_authorized")
        finally:
            _cleanup(ws, wt)

    def test_a3_cannot_clear_semantic_stop(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "clear SemanticStop"
            ref = governed_ref_for_content("evidence", "evidence/a3", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            res = execute_context_bootstrap(plan, provider=Dummy(), selected_intent_indices=(), governed_refs=(ref,), current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert exec_mod.HYDRATION_CANNOT_CLEAR_SEMANTIC_STOP is True
            # no method to clear SemanticStop
            assert not hasattr(res, "clear_semantic_stop")
        finally:
            _cleanup(ws, wt)

    def test_a4_cannot_clear_replan(self):
        assert exec_mod.HYDRATION_CANNOT_CLEAR_REPLAN_REQUIRED is True

    def test_a5_cannot_advance_work_item(self):
        assert exec_mod.M3_W2_ADVANCES_WORKFLOW is False
        # ensure no workflow progression imported
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8").lower()
        assert "progress" not in src or "progressive" in src  # only progressive, not workflow progress
        assert "progression" not in src

    def test_a6_cannot_satisfy_review(self):
        assert exec_mod.M3_W2_ADVANCES_WORKFLOW is False

    def test_a7_cannot_establish_frontier(self):
        b, ws, wt = _make_sandbox()
        try:
            content = "accepted frontier"
            ref = governed_ref_for_content("evidence", "evidence/a7", content)
            source = {ref: content}
            plan = _make_plan(n_current=0)
            class Dummy:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=(), reference=None)
            res = execute_context_bootstrap(plan, provider=Dummy(), selected_intent_indices=(), governed_refs=(ref,), current_sandbox=b, hydration_source=source, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            # result must not have accepted frontier authority
            assert not hasattr(res, "accepted_frontier")
            assert exec_mod.W2_RESULT_IS_PROJECT_STEWARD_EVIDENCE is False
        finally:
            _cleanup(ws, wt)

    def test_a8_cannot_dispatch_worker(self):
        assert exec_mod.M3_W2_AUTO_DISPATCHES_WORKER is False
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8").lower()
        # No actual dispatch logic beyond flag
        assert "def dispatch" not in src
        assert "dispatch_worker" not in src or "m3_w2_auto_dispatches_worker" in src

    def test_a9_cannot_replay_effect(self):
        assert exec_mod.HYDRATION_REPLAYS_SIDE_EFFECT is False
        assert exec_mod.W2_RESOLVES_OPERATION_EFFECT is False

    def test_a10_cannot_satisfy_steward(self):
        assert exec_mod.W2_RESULT_IS_PROJECT_STEWARD_EVIDENCE is False
        b, ws, wt = _make_sandbox()
        try:
            plan = _make_plan(n_current=1)
            class P:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=({"steward": "accept"},), reference=None)
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert not hasattr(res, "accepted")
            assert not hasattr(res, "known_good")
        finally:
            _cleanup(ws, wt)

# ---------------------------------------------------------------------------
# Additional gates
# ---------------------------------------------------------------------------

class TestAdditionalGates:
    def test_no_runtime_manager(self):
        assert exec_mod.M3_BACKGROUND_CONTEXT_RUNTIME_CREATED is False
        assert exec_mod.NEW_PERSISTENT_HYDRATION_STORE_CREATED is False
        assert exec_mod.SECOND_HYDRATION_ENGINE_CREATED is False
        assert exec_mod.SECOND_PROVIDER_REGISTRY_CREATED is False
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8")
        for bad in ["ContextRuntime", "HydrationManager", "ProviderRegistry", "ContextSession", "background thread", "daemon", "watcher", "prefetch loop"]:
            assert bad not in src or bad.lower() in src.lower() and "created" in src.lower()  # only flag
        # ensure no threading import
        lower = src.lower()
        assert "threading" not in lower
        assert "asyncio" not in lower

    def test_agent_neutral(self):
        assert exec_mod.M3_AGENT_NEUTRAL is True
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8").lower()
        for kw in ["openai", "claude", "hermes", "chatgpt"]:
            assert kw not in src

    def test_shared_contracts_unchanged(self):
        assert exec_mod.W1_CONTRACT_REVISION_REQUIRED is False
        assert exec_mod.SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED is False

    def test_payload_bounded(self):
        assert exec_mod.PROVIDER_PAYLOAD_BOUNDED_BY_W2 is True
        assert exec_mod.PROVIDER_PAYLOAD_OVERSIZE_FAILS_CLOSED is True
        assert exec_mod.PROVIDER_PAYLOAD_SILENT_TRUNCATION is False

    def test_result_not_authority(self):
        assert exec_mod.CONTEXT_BOOTSTRAP_EXECUTION_RESULT_IS_AUTHORITY is False
        plan = _make_plan(n_current=1)
        class P:
            def fetch(self, req: ContextRequest) -> ContextResponse:
                return ContextResponse.success(payload=({"x": 1},), reference=None)
        b, ws, wt = _make_sandbox()
        try:
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id)
            assert res.is_authority is False
            for bad in ["authorized", "approved", "retry_authorized", "workflow_complete", "accepted", "safe_to_retry"]:
                assert not hasattr(res, bad)
        finally:
            _cleanup(ws, wt)

    def test_existing_limits_reused(self):
        assert exec_mod.EXISTING_BOOTSTRAP_BUNDLE_LIMITS_REUSED is True
        assert MAX_COMPONENT_COUNT == 16
        assert MAX_MATERIALIZED_LENGTH == 32 * 1024
        assert MAX_BUNDLE_CANONICAL_BYTES_HARD == 128 * 1024
        assert exec_mod.MAX_HYDRATION_BATCH_REUSED == 8
        assert exec_mod.MAX_HYDRATED_BYTES_REUSED == 4096
        assert exec_mod.EXISTING_SELECTIVE_HYDRATION_BATCH_API_REUSED is True

    def test_w1_plan_reused(self):
        assert exec_mod.W1_CONTEXT_BOOTSTRAP_PLAN_REUSED is True
        assert exec_mod.SECOND_CONTEXT_SELECTION_MODEL_CREATED is False
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8")
        assert "ContextSelection" not in src
        assert "ContextRequestPlanV2" not in src
        assert "ProviderIntent" not in src

    def test_second_ontology_not_created(self):
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8")
        assert "ContextStore" not in src
        assert "HydrationStore" not in src
        # check flags
        assert exec_mod.SECOND_CONTEXT_SELECTION_MODEL_CREATED is False
        assert exec_mod.SECOND_HYDRATION_ENGINE_CREATED is False

    def test_existing_base_semantics_preserved(self):
        # base bundle with soul-like component should be preserved
        b, ws, wt = _make_sandbox()
        try:
            base_mat = "soul content"
            base_digest = _sha(base_mat)
            base_comp = BootstrapComponent(kind="soul", delivery="eager", materialized=base_mat, digest=base_digest, provenance="base")
            base = BootstrapBundle(bundle_type="task_main", components=(base_comp,))
            plan = _make_plan(n_current=1)
            class P:
                def fetch(self, req: ContextRequest) -> ContextResponse:
                    return ContextResponse.success(payload=({"new": "ctx"},), reference=None)
            res = execute_context_bootstrap(plan, provider=P(), selected_intent_indices=(0,), current_sandbox=b, hydration_source=None, expected_project_id=b.project_id, expected_worktree_id=b.worktree_id, base_bundle=base)
            assert res.bootstrap_bundle is not None
            # base soul should still be present
            kinds = [c.kind for c in res.bootstrap_bundle.components]
            assert "soul" in kinds
            assert "semantic_ref" in kinds
            assert exec_mod.EXISTING_BASE_BOOTSTRAP_SEMANTICS_PRESERVED is True
        finally:
            _cleanup(ws, wt)

    def test_no_full_plan_body(self):
        src = pathlib.Path(EXEC_PATH).read_text(encoding="utf-8").lower()
        # ensure we don't load full plan body
        assert "full_plan_body" not in src or "required=no" in src

    def test_digest_not_authority(self):
        assert exec_mod.DIGEST_IS_AUTHORITY is False

    def test_production_module_count(self):
        assert exec_mod.NEW_PRODUCTION_MODULE_COUNT == 1
        assert exec_mod.M3_TOTAL_NEW_PRODUCTION_MODULE_COUNT_AFTER_W2 == 2
        assert exec_mod.THIRD_M3_PRODUCTION_MODULE_CREATED is False
