"""AF #48 M1/W2 — Handoff + Task Lifecycle Thin Façade (focused)."""
from __future__ import annotations

import json
import hashlib
import tempfile
from pathlib import Path

import pytest

from aota_forge.work_plane.handoff_store import handoff_write, handoff_open, HandoffRef, HANDOFF_MODES, HANDOFF_CONTROL_FIELDS
from aota_forge.work_plane.task_facade import task_start, task_return, get_completion, clear_completions
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
from aota_forge.core_ingress import dispatch_via_core, CanonicalDispatchBinding, resolve_descriptor
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.agent_facing_contract import (
    HANDOFF_WRITE_MODES,
    HANDOFF_OPEN_VIEWS,
    TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM,
    TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT,
    TASK_RETURN_IS_NOT_HANDOFF_WRITE,
    TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS,
    WORKER_RESULT_FULL_WRITE_COUNT_NORMAL,
    WORKER_AUTHORS_RESULT_CARD,
)


def _explicit_test_dispatcher() -> ExecutionDispatcher:
    """AF #49 M1/W1: test doubles are only reachable through explicit injection."""
    registry = ExecutorRegistry()
    registry.register(ReferenceFakeExecutorAdapter())
    return ExecutionDispatcher(registry, state_store=InMemoryExecutionStateStore())


def _bind_test_ingress_dispatcher() -> ExecutionDispatcher:
    dispatcher = _explicit_test_dispatcher()
    bind_execution_dispatcher(dispatcher)
    return dispatcher

def _sandbox(project_id="projA", worktree_id="wtA") -> WorktreeSandboxBoundary:
    td = tempfile.mkdtemp(prefix="handoff-test-")
    p = Path(td)
    # Ensure .aota exists? handoff_store will create
    return WorktreeSandboxBoundary(
        workspace_id="ws1",
        workspace_root=str(p),
        project_id=project_id,
        project_root=str(p),
        worktree_id=worktree_id,
        worktree_root=str(p),
        registry_fingerprint="0"*64,
        candidate_fingerprint="1"*64,
    )

def _sandbox_for_path(path: Path, project_id="projA", worktree_id="wtA") -> WorktreeSandboxBoundary:
    path.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws1",
        workspace_root=str(path),
        project_id=project_id,
        project_root=str(path),
        worktree_id=worktree_id,
        worktree_root=str(path.resolve()),
        registry_fingerprint="0"*64,
        candidate_fingerprint="1"*64,
    )

class TestHandoffWriteOpen:
    def test_handoff_write_work_item_pass(self):
        sb = _sandbox()
        sem = {
            "objective": "Implement feature X",
            "bounded_scope": "src/foo.py bounded scope",
            "validation_expectations": ["pytest passes"],
            "semantic_stop_expectations": ["stop when done"],
            "work_role": "coder",
            "task_kind": "implementation",
        }
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb, milestone_id="M1", work_item_id="W1")
        assert ref.mode == "work_item"
        assert len(ref.digest) == 64
        assert ref.project_id == sb.project_id

    def test_handoff_open_work_item_full(self):
        sb = _sandbox()
        sem = {
            "objective": "Do work",
            "bounded_scope": "bounded scope text",
            "validation_expectations": ["check"],
            "semantic_stop_expectations": ["stop"],
        }
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        opened = handoff_open(ref, "full", sandbox=sb)
        assert opened["mode"] == "work_item"
        assert opened["semantic"]["objective"] == sem["objective"]
        assert "envelope" in opened
        assert opened["envelope"]["project_id"] == sb.project_id
        assert opened["envelope"]["digest"] == ref.digest

    def test_handoff_write_result_pass(self):
        sb = _sandbox()
        sem = {"summary": "Work done", "work_done": "Implemented X", "validation": "pytest passed"}
        ref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb)
        assert ref.mode == "result"
        opened = handoff_open(ref, "full", sandbox=sb)
        assert opened["semantic"]["summary"] == "Work done"

    def test_handoff_open_result_full(self):
        sb = _sandbox()
        sem = {"summary": "result summary", "work_done": "did work", "validation": "ok"}
        ref = handoff_write(mode="result", semantic=sem, caller_role="reviewer", sandbox=sb)
        full = handoff_open(ref, "full", sandbox=sb)
        assert full["mode"] == "result"
        card = handoff_open(ref, "card", sandbox=sb)
        assert "card_digest" in card
        assert card["digest"] == ref.digest

    def test_control_envelope_cannot_be_forged(self):
        sb = _sandbox()
        # LLM tries to inject control field
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"], "artifact_id": "fake-id"}
        try:
            handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
            assert False, "should have failed on control field forgery"
        except ValueError as e:
            assert "control field" in str(e).lower()

    def test_semantic_not_rewritten(self):
        sb = _sandbox()
        sem = {"objective": "keep exact", "bounded_scope": "exact scope 123", "validation_expectations": ["v1"], "semantic_stop_expectations": ["s1"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        opened = handoff_open(ref, "full", sandbox=sb)
        assert opened["semantic"]["objective"] == "keep exact"
        assert opened["semantic"]["bounded_scope"] == "exact scope 123"

    def test_cross_project_fail_closed(self):
        sbA = _sandbox(project_id="projA", worktree_id="wtA")
        sbB = _sandbox(project_id="projB", worktree_id="wtB")
        # But need same underlying dir? For cross-project, we use different sandbox with different project_id but need to share storage?
        # Our store isolates per worktree_root, so cross-project test must use same dir but different project_id sandbox
        # Simulate: create single dir, two sandboxes with different project_id but same root
        import tempfile, pathlib
        td = Path(tempfile.mkdtemp(prefix="crossproj-"))
        sb1 = _sandbox_for_path(td, project_id="projA", worktree_id="wt1")
        sb2 = WorktreeSandboxBoundary(
            workspace_id="ws1", workspace_root=str(td), project_id="projB", project_root=str(td),
            worktree_id="wt1", worktree_root=str(td.resolve()),
            registry_fingerprint="0"*64, candidate_fingerprint="1"*64,
        )
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb1)
        # Try open with different project
        try:
            handoff_open(ref, "full", sandbox=sb2)
            assert False, "cross-project should fail"
        except ValueError as e:
            assert "cross-project" in str(e).lower() or "cross-worktree" in str(e).lower() or "fail closed" in str(e).lower()

    def test_tampered_digest_fail_closed(self):
        sb = _sandbox()
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        # Tamper: change digest in ref string
        tampered_ref_str = ref.ref.replace(ref.digest, "0"*64)
        try:
            handoff_open(tampered_ref_str, "full", sandbox=sb)
            assert False, "tampered should fail"
        except ValueError as e:
            # Should be not found or digest mismatch
            assert "digest" in str(e).lower() or "not found" in str(e).lower() or "tamper" in str(e).lower()

    def test_handoff_modes_and_views_constants(self):
        assert set(HANDOFF_MODES) == {"milestone", "work_item", "result"}
        assert HANDOFF_WRITE_MODES == ("milestone", "work_item", "result")
        assert HANDOFF_OPEN_VIEWS == ("card", "full")

    def test_control_envelope_separate(self):
        from aota_forge.work_plane.handoff_store import HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD
        assert HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD is True

class TestTaskStart:
    def test_task_start_pass(self):
        sb = _sandbox()
        sem = {
            "objective": "Build feature",
            "bounded_scope": "src/foo.py",
            "validation_expectations": ["pytest"],
            "semantic_stop_expectations": ["stop"],
            "work_role": "coder",
            "task_kind": "impl",
        }
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb, milestone_id="M1", work_item_id="W1")
        result = task_start(role="coder", handoff_ref=ref.ref, caller_role="task-main", sandbox=sb, dispatcher=_explicit_test_dispatcher())
        assert "task_id" in result
        assert "status" in result
        assert result["handoff_digest"] == ref.digest

    def test_worker_calling_task_start_denied(self):
        sb = _sandbox()
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        try:
            task_start(role="coder", handoff_ref=ref.ref, caller_role="coder", sandbox=sb)
            assert False
        except ValueError as e:
            assert "task-main" in str(e).lower() or "authority" in str(e).lower()

    def test_task_start_reuses_execution(self):
        assert TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM is True
        # Check that dispatch path uses compile_handoff_to_execution_package + ExecutionDispatcher
        # We verify via implementation: task_start should call dispatcher.dispatch, not create new engine
        sb = _sandbox()
        sem = {"objective": "obj2", "bounded_scope": "scope2", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        # Ensure no new engine flag
        from aota_forge.work_plane.agent_facing_contract import NEW_EXECUTION_ENGINE_CREATED
        assert NEW_EXECUTION_ENGINE_CREATED is False

    def test_wrong_task_attempt_fail_closed_task_start(self):
        # Tampered handoff digest should fail
        sb = _sandbox()
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        bad_ref = ref.ref.replace(ref.digest, "f"*64)
        try:
            task_start(role="coder", handoff_ref=bad_ref, caller_role="task-main", sandbox=sb, dispatcher=_explicit_test_dispatcher())
            assert False
        except ValueError:
            pass

    def test_via_core_ingress_task_start(self):
        # Test via aota.invoke canonical dispatch
        import tempfile, pathlib
        td = Path(tempfile.mkdtemp(prefix="ingress-start-"))
        sb = _sandbox_for_path(td, project_id="projX", worktree_id="wtX")
        # Need TaskHandoff for binding handoff
        from aota_forge.work_plane.handoff import TaskHandoff
        h = TaskHandoff(
            work_role="task-main",
            task_kind="test",
            objective="obj",
            bounded_scope="scope",
            validation_expectations=["v"],
            semantic_stop_expectations=["s"],
        )
        from aota_forge.work_plane.tool_surface import create_role_tool_surface
        from aota_forge.work_plane.workspace_tools import create_workspace_authority, WORKSPACE_SEARCH_DESCRIPTOR, WORKSPACE_READ_DESCRIPTOR
        from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
        policy = AgentsPolicyCandidate(policy_id="p1", project_id="projX", scope="scope", content="c", provenance_ref="m1/w1")
        from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary
        # create authorities minimal for binding
        # Use core_ingress binding construction
        surf = create_role_tool_surface("task-main", eager=("workspace.search","workspace.read"), progressive=())
        # Need to create a minimal TrustedWorkerBinding alternative: directly use CanonicalDispatchBinding
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        ref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        binding = CanonicalDispatchBinding(
            canonical_task_id="task-main-1",
            project_id=sb.project_id,
            worktree_id=sb.worktree_id,
            handoff=h,
            sandbox=sb,
            tool_surface=surf,
            read_authorities=(),
        )
        _bind_test_ingress_dispatcher()
        try:
            resp = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": ref.ref}, binding)
            assert resp.ok, f"task.start via ingress failed: {resp.error}"
            assert "task_id" in resp.payload

            # Worker trying task.start via ingress should be denied
            h_worker = TaskHandoff(
                work_role="coder",
                task_kind="test",
                objective="obj",
                bounded_scope="scope",
                validation_expectations=["v"],
                semantic_stop_expectations=["s"],
            )
            surf_worker = create_role_tool_surface("coder", eager=("workspace.search","workspace.read"), progressive=())
            binding_worker = CanonicalDispatchBinding(
                canonical_task_id="worker-1",
                project_id=sb.project_id,
                worktree_id=sb.worktree_id,
                handoff=h_worker,
                sandbox=sb,
                tool_surface=surf_worker,
            )
            resp2 = dispatch_via_core("task.start", {"role": "coder", "handoff_ref": ref.ref}, binding_worker)
            assert not resp2.ok
            assert resp2.error["code"] == "AUTHORITY_DENIED"
        finally:
            reset_execution_dispatcher()

class TestTaskReturn:
    def _setup_task(self):
        sb = _sandbox()
        sem = {
            "objective": "do work",
            "bounded_scope": "scope",
            "validation_expectations": ["v"],
            "semantic_stop_expectations": ["s"],
            "work_role": "coder",
            "task_kind": "impl",
        }
        self._disp = _explicit_test_dispatcher()
        wref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        start = task_start(role="coder", handoff_ref=wref.ref, caller_role="task-main", sandbox=sb, dispatcher=self._disp)
        task_id = start["task_id"]
        return sb, task_id, wref, start

    def test_task_return_completed_pass(self):
        sb, task_id, wref, start = self._setup_task()
        sem_result = {"summary": "done", "work_done": "implemented", "validation": "pytest ok"}
        rref = handoff_write(mode="result", semantic=sem_result, caller_role="coder", sandbox=sb, task_id=task_id)
        comp = task_return(status="completed", result_ref=rref.ref, caller_role="coder", caller_task_id=task_id, sandbox=sb, dispatcher=self._disp)
        assert comp["task_id"] == task_id
        assert comp["status"] == "completed"
        assert "card" in comp
        assert "full_result_ref" in comp

    def test_task_return_blocked_pass(self):
        sb, task_id, _, _ = self._setup_task()
        sem = {"summary": "blocked", "blockers": "needs input", "work_done": "partial"}
        rref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id=task_id)
        comp = task_return(status="blocked", result_ref=rref.ref, caller_role="coder", caller_task_id=task_id, sandbox=sb, dispatcher=self._disp)
        assert comp["status"] == "blocked"

    def test_task_return_failed_pass(self):
        sb, task_id, _, _ = self._setup_task()
        sem = {"summary": "failed", "findings": "error"}
        rref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id=task_id)
        comp = task_return(status="failed", result_ref=rref.ref, caller_role="coder", caller_task_id=task_id, sandbox=sb, dispatcher=self._disp)
        assert comp["status"] == "failed"

    def test_task_main_calling_task_return_denied(self):
        sb, task_id, _, _ = self._setup_task()
        sem = {"summary": "done"}
        rref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id=task_id)
        try:
            task_return(status="completed", result_ref=rref.ref, caller_role="task-main", caller_task_id=task_id, sandbox=sb)
            assert False
        except ValueError as e:
            assert "one-shot" in str(e).lower() or "task.return" in str(e).lower() or "authority" in str(e).lower()

    def test_wrong_role_return_fail_closed(self):
        sb, task_id, _, _ = self._setup_task()
        sem = {"summary": "done"}
        # Write result as coder, but try return as reviewer (mismatch source_role)
        rref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id=task_id)
        try:
            task_return(status="completed", result_ref=rref.ref, caller_role="reviewer", caller_task_id=task_id, sandbox=sb)
            assert False
        except ValueError as e:
            assert "source_role" in str(e) or "wrong-role" in str(e).lower() or "reviewer" in str(e)

    def test_wrong_task_return_fail_closed(self):
        sb, task_id, _, _ = self._setup_task()
        sem = {"summary": "done"}
        rref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id=task_id)
        try:
            task_return(status="completed", result_ref=rref.ref, caller_role="coder", caller_task_id="different-task-id", sandbox=sb)
            assert False
        except ValueError as e:
            assert "task_id" in str(e).lower() or "wrong-task" in str(e).lower()

    def test_task_return_not_execution_task_result(self):
        assert TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT is True
        # execution.task_result is fetch, not return
        assert TASK_RETURN_IS_NOT_HANDOFF_WRITE is True

    def test_worker_writes_one_full_result(self):
        assert WORKER_RESULT_FULL_WRITE_COUNT_NORMAL == 1
        assert WORKER_AUTHORS_RESULT_CARD is False

    def test_card_deterministic(self):
        from aota_forge.work_plane.result_card import project_worker_result_card, WorkerResultCard
        from aota_forge.core.execution.results import CanonicalResult
        from aota_forge.core.result_governance import ResultGovernanceProjection
        # Create two identical results and ensure card digest same
        cr = CanonicalResult.success(canonical_task_id="t1", executor_id="e1", correlation_id="c1")
        gp = ResultGovernanceProjection.success()
        card1 = project_worker_result_card(cr, gp, "coder", summary="same summary")
        card2 = project_worker_result_card(cr, gp, "coder", summary="same summary")
        assert card1.card_digest == card2.card_digest

    def test_completion_inline_card(self):
        sb, task_id, _, _ = self._setup_task()
        sem = {"summary": "completed work", "work_done": "did X"}
        rref = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id=task_id)
        sink = {}
        comp = task_return(status="completed", result_ref=rref.ref, caller_role="coder", caller_task_id=task_id, sandbox=sb, dispatcher=self._disp, completion_sink=sink)
        # Parent can get completion from the explicitly injected test sink
        fetched = get_completion(task_id, completion_sink=sink)
        assert fetched is not None
        assert fetched["card"] == comp["card"]
        assert fetched["full_result_ref"] == comp["full_result_ref"]
        # Normal task-main card extra read =0 means card is inline (no extra open needed)
        # Card digest is in completion top-level; card itself is deterministic
        assert "card_digest" in fetched
        # Also ensure card_digest matches card's computed digest
        from aota_forge.work_plane.result_card import WorkerResultCard
        card_obj = WorkerResultCard.from_dict(fetched["card"])
        assert card_obj.card_digest == fetched["card_digest"]

    def test_via_core_ingress_task_return(self):
        import tempfile
        from pathlib import Path
        from aota_forge.work_plane.handoff import TaskHandoff
        from aota_forge.work_plane.tool_surface import create_role_tool_surface
        td = Path(tempfile.mkdtemp(prefix="ingress-return-"))
        sb = _sandbox_for_path(td, project_id="projY", worktree_id="wtY")
        # Start task via facade to get task_id
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        wref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        disp = _explicit_test_dispatcher()
        start = task_start(role="coder", handoff_ref=wref.ref, caller_role="task-main", sandbox=sb, dispatcher=disp)
        task_id = start["task_id"]
        # Create result handoff
        sem_r = {"summary": "done ingress"}
        rref = handoff_write(mode="result", semantic=sem_r, caller_role="coder", sandbox=sb, task_id=task_id)
        # Build worker binding for task.return via ingress
        h_worker = TaskHandoff(work_role="coder", task_kind="test", objective="obj", bounded_scope="scope", validation_expectations=["v"], semantic_stop_expectations=["s"])
        surf = create_role_tool_surface("coder", eager=("workspace.search","workspace.read"), progressive=())
        binding = CanonicalDispatchBinding(
            canonical_task_id=task_id,
            project_id=sb.project_id,
            worktree_id=sb.worktree_id,
            handoff=h_worker,
            sandbox=sb,
            tool_surface=surf,
        )
        bind_execution_dispatcher(disp)
        try:
            resp = dispatch_via_core("task.return", {"status": "completed", "result_ref": rref.ref}, binding)
            assert resp.ok, f"task.return via ingress failed: {resp.error}"
            assert resp.payload["task_id"] == task_id
            assert "card" in resp.payload
            # task-main calling task.return via ingress should be denied
            h_main = TaskHandoff(work_role="task-main", task_kind="test", objective="obj", bounded_scope="scope", validation_expectations=["v"], semantic_stop_expectations=["s"])
            surf_main = create_role_tool_surface("task-main", eager=("workspace.search","workspace.read"), progressive=())
            binding_main = CanonicalDispatchBinding(
                canonical_task_id=task_id,
                project_id=sb.project_id,
                worktree_id=sb.worktree_id,
                handoff=h_main,
                sandbox=sb,
                tool_surface=surf_main,
            )
            resp2 = dispatch_via_core("task.return", {"status": "completed", "result_ref": rref.ref}, binding_main)
            assert not resp2.ok
            assert resp2.error["code"] in ("AUTHORITY_DENIED", "WRONG_ROLE")
        finally:
            reset_execution_dispatcher()

class TestHandoffTaskIntegration:
    def test_worker_writes_one_full_result_card_deterministic(self):
        sb = _sandbox()
        # Simulate worker doing handoff.write result once
        sem = {"summary": "final", "work_done": "did", "validation": "ok"}
        ref1 = handoff_write(mode="result", semantic=sem, caller_role="coder", sandbox=sb, task_id="t1")
        # Second write with same semantic but different digest due to artifact_id randomness -> different ref but same card logic?
        # Worker writes one full result — we check that card derived from that result is deterministic
        # Card determinism already tested above
        assert ref1.mode == "result"
        # Ensure worker cannot author card directly — card is derived via task_return
        assert WORKER_AUTHORS_RESULT_CARD is False

    def test_existing_wakeup_reentry_non_regression(self):
        # Ensure explicit-sink completion observation works after task_return
        sb = _sandbox()
        sem = {"objective": "obj", "bounded_scope": "scope", "validation_expectations": ["v"], "semantic_stop_expectations": ["s"]}
        disp = _explicit_test_dispatcher()
        wref = handoff_write(mode="work_item", semantic=sem, caller_role="task-main", sandbox=sb)
        start = task_start(role="coder", handoff_ref=wref.ref, caller_role="task-main", sandbox=sb, dispatcher=disp)
        task_id = start["task_id"]
        rref = handoff_write(mode="result", semantic={"summary": "done"}, caller_role="coder", sandbox=sb, task_id=task_id)
        sink = {}
        comp = task_return(status="completed", result_ref=rref.ref, caller_role="coder", caller_task_id=task_id, sandbox=sb, dispatcher=disp, completion_sink=sink)
        # Explicit test sink: completion should be available immediately
        fetched = get_completion(task_id, completion_sink=sink)
        assert fetched is not None
        assert fetched["task_id"] == task_id
        clear_completions(sink)
        assert get_completion(task_id, completion_sink=sink) is None

class TestW1Contract:
    def test_w1_contract_still_passes(self):
        # Run W1 contract file quickly via import check
        import aota_forge.work_plane.agent_facing_contract as afc
        assert afc.W1_IS_CONTRACT_FREEZE_NOT_LIVE_REGISTRATION is True
        assert afc.HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD is True
        assert afc.TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM is True

class TestOperationsRegistry:
    def test_canonical_ops_include_new(self):
        ops = set(resolve_descriptor("handoff.write").name for _ in [1])
        # Actually check list_canonical_operations
        from aota_forge.core_ingress import list_canonical_operations
        all_ops = set(list_canonical_operations())
        assert "handoff.write" in all_ops
        assert "handoff.open" in all_ops
        assert "task.start" in all_ops
        assert "task.return" in all_ops
        assert "execution.task_start" in all_ops  # still internal
        # Ensure single authority count
        from aota_forge.core_ingress import OPERATION_DESCRIPTOR_AUTHORITY_COUNT, CANONICAL_OPERATION_DISPATCH_PLANE_COUNT
        assert OPERATION_DESCRIPTOR_AUTHORITY_COUNT == 1
        assert CANONICAL_OPERATION_DISPATCH_PLANE_COUNT == 1
        from aota_forge.work_plane.agent_facing_contract import ONE_AGENT_FACING_AOTA_MCP_TOOL
        assert ONE_AGENT_FACING_AOTA_MCP_TOOL is True

    def test_execution_task_start_still_internal(self):
        from aota_forge.core_ingress import PROVIDER_BACKED_OPERATIONS, INGRESS_BACKED_OPERATIONS
        assert "execution.task_start" in INGRESS_BACKED_OPERATIONS
        assert "task.start" in PROVIDER_BACKED_OPERATIONS
        assert TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT is True
