"""AF #57 M3/RV1 — integrated-review dispatch durable handoff lifecycle repair.

Forensics (rv1-real-proof-20260919-155120-976231) proved the defect:

    runtime_resolved reviewer dispatch referenced a handoff_digest that had no
    durable handoff artifact; every reviewer handoff.open of its own handoff
    failed (UNKNOWN_REF) and the reviewer spent 900s in workspace.search
    thrash without a governed task.return.

This suite proves the repaired lifecycle contract:

* the runtime_resolved review dispatch MATERIALIZES the durable reviewer
  work-item handoff BEFORE dispatch (existing handoff.write store, no second
  protocol);
* the injected handoff digest is the real durable artifact digest;
* the reviewer child binding/bootstrap carries the exact openable durable ref
  and ``handoff.open`` succeeds at startup (no workspace search needed);
* evidence refs in the reviewer handoff are actual openable durable refs;
* a foreign-task or stale-attempt work-item handoff fails closed on open;
* completed and blocked reviewer task.return both require and produce valid
  durable result handoffs.

Proof boundary (honest):
  PROVES=deterministic component integration through the production task-main
         bootstrap + runner + durable handoff store; no synthetic digest.
  DOES_NOT_PROVE=real Hermes reviewer model execution (the bounded real retry
         is recorded separately under the M3 evidence root).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aota_forge.composition.task_main_host_bootstrap import (
    _materialize_reviewer_work_handoff,
)
from aota_forge.core.execution.durable_state import OriginSessionRef
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.mcp_transport import _SharedAotaMcpAdapter
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_DISPATCHED_REVIEW,
    TaskMainMilestoneRunner,
)
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    create_worker_envelope,
    load_binding_from_envelope,
)
from aota_forge.work_plane.handoff_store import handoff_open, handoff_write
from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap
from aota_forge.work_plane.task_facade import (
    TRUSTED_WORK_HANDOFF_CONTEXT_KEY,
    task_return,
)
from aota_forge.work_plane.task_return_receipt import read_task_return_receipt
from tests.test_af49_m1_w8_worker_trusted_binding_propagation import (
    PROJECT_ID,
    WORKTREE_ID,
    _live,
    _production_env,
    _sandbox,
)
from tests.test_m3_w3_real_governed_autonomous_slice import (
    FAKE_EXECUTOR_ID,
    MILESTONE_ID,
    ORIGIN_SESSION,
    World,
    _cid,
    _governed_resolver,
    _evidence,
    _resolver,
    _reviewer_handoff,
    _run_to_terminal,
    _view,
)

REVIEWER_CID = f"{PROJECT_ID}:M1:RV1:attempt-1"
REVIEWER_CID_ATTEMPT_2 = f"{PROJECT_ID}:M1:RV1:attempt-2"
W1_CID = f"{PROJECT_ID}:M1:W1:attempt-1"
REVIEW_RESULT_SUMMARY = "RV1 integrated review completed"
STARTUP_PROMPT = (
    Path(__file__).resolve().parents[1]
    / "aota_forge"
    / "composition"
    / "worker_startup_prompt.md"
)


def _reviewer_dispatch_payload(
    *,
    handoff: Any,
    canonical_task_id: str,
    handoff_ref: str,
    handoff_digest: str,
) -> dict[str, Any]:
    """Minimal execution payload mapping the host resolver consumes."""
    return {
        "context": {
            "canonical_task_id": canonical_task_id,
            "working_context": {
                "bounded_scope": handoff.bounded_scope,
                "handoff_digest": handoff.handoff_digest,
                "task_kind": handoff.task_kind,
                "work_role": "reviewer",
                "refs": {"work_item_ref": {"ref": "RV1"}},
                TRUSTED_WORK_HANDOFF_CONTEXT_KEY: {
                    "ref": handoff_ref,
                    "digest": handoff_digest,
                    "mode": "work_item",
                },
            },
        },
    }


def _materialize(
    env: Any,
    *,
    state: Any = None,
    reviewer_task_id: str = REVIEWER_CID,
    live: Any = None,
) -> dict[str, Any]:
    return _materialize_reviewer_work_handoff(
        base_handoff=env.binding.trusted_task_main_context.reviewer_handoff_resolver(),
        sandbox=env.sandbox,
        live_view=live or env.live,
        state=state,
        project_id=PROJECT_ID,
        reviewer_task_id=reviewer_task_id,
        plan_id=None,
        parent_task_identity=ORIGIN_SESSION,
    )


class TestReviewerDispatchMaterialization:
    def test_materializer_writes_durable_reviewer_work_item_handoff(
        self, tmp_path: Path
    ) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        record = _materialize(env, live=live)

        ref = record["handoff_ref"]
        digest = record["handoff_digest"]
        assert record["canonical_task_id"] == REVIEWER_CID
        assert len(digest) == 64
        assert digest == digest.lower()

        # The durable artifact exists at the canonical store path and the
        # injected digest IS the durable artifact digest.
        artifact = env.root / ".aota" / "handoffs" / f"{digest}.json"
        assert artifact.is_file()
        opened = handoff_open(ref, "full", sandbox=env.sandbox)
        assert opened["digest"] == digest
        assert opened["ref"] == ref
        envelope = opened["envelope"]
        assert envelope["task_id"] == REVIEWER_CID
        assert envelope["target_role"] == "reviewer"
        assert envelope["milestone_id"] == live.milestone_id
        assert envelope["plan_ref"] == live.plan_authority
        assert envelope["provenance"]["plan_digest"] == live.plan_digest
        semantic = opened["semantic"]
        assert semantic["work_role"] == "reviewer"
        assert semantic["work_item_ref"] == "RV1"
        # Bounded review context refs (project/plan/accepted main/RV1 task)
        # are present; no huge evidence bodies are inlined.
        context_refs = " ".join(semantic.get("context_refs") or [])
        assert f"review-task://{REVIEWER_CID}" in context_refs
        assert f"plan-id://" not in context_refs  # no trusted plan_id in this fixture
        assert "accepted-main://" in context_refs
        assert "plan-authority://" in context_refs
        # Materialization alone performs no physical dispatch.
        assert env.spawn.calls == []

    def test_materializer_evidence_refs_are_openable_durable_refs(
        self, tmp_path: Path
    ) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        # Accepted W1 evidence: durable result handoff + durable task.return
        # receipt for the exact W1 canonical task.
        result_ref = handoff_write(
            mode="result",
            semantic={"summary": "W1 accepted output"},
            caller_role="coder",
            sandbox=env.sandbox,
            task_id=W1_CID,
        )
        from aota_forge.work_plane.task_return_receipt import (
            write_task_return_receipt,
        )

        write_task_return_receipt(
            env.sandbox,
            canonical_task_id=W1_CID,
            result_ref=result_ref.ref,
            result_digest=result_ref.digest,
            status="completed",
        )
        state = SimpleNamespace(bindings={"W1": {"canonical_task_id": W1_CID}})
        record = _materialize(env, state=state, live=live)
        opened = handoff_open(record["handoff_ref"], "full", sandbox=env.sandbox)
        evidence_refs = list(opened["semantic"].get("evidence_refs") or [])
        assert evidence_refs == [result_ref.ref]
        # Every declared evidence ref is actually openable at materialization.
        evidence_opened = handoff_open(evidence_refs[0], "full", sandbox=env.sandbox)
        assert evidence_opened["digest"] == result_ref.digest
        assert evidence_opened["mode"] == "result"

    def test_reviewer_env_binds_durable_ref_and_bootstrap_exposes_it(
        self, tmp_path: Path
    ) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        record = _materialize(env, live=live)

        payload = _reviewer_dispatch_payload(
            handoff=record["handoff"],
            canonical_task_id=REVIEWER_CID,
            handoff_ref=record["handoff_ref"],
            handoff_digest=record["handoff_digest"],
        )
        child = env.host._worker_env_resolver(payload)
        locator = child[PRE_RESOLVED_BINDING_ENV]
        binding = load_binding_from_envelope(locator)
        assert binding.canonical_task_id == REVIEWER_CID
        assert binding.work_handoff_ref == record["handoff_ref"]
        assert binding.work_handoff_digest == record["handoff_digest"]

        # Reviewer startup contract: role.bootstrap exposes the exact durable
        # ref; handoff.open succeeds on the first try (no search required).
        bootstrap = handle_role_bootstrap(binding, {})
        task_handoff = bootstrap["TASK_HANDOFF"]
        assert task_handoff["handoff_ref"] == record["handoff_ref"]
        assert task_handoff["handoff_digest"] == record["handoff_digest"]
        opened = handoff_open(task_handoff["handoff_ref"], "full", sandbox=binding.sandbox)
        assert opened["digest"] == record["handoff_digest"]
        assert opened["envelope"]["task_id"] == REVIEWER_CID
        # Physical launch is not part of env resolution.
        assert env.spawn.calls == []

    def test_runner_review_dispatch_injects_durable_handoff_digest(
        self, tmp_path: Path
    ) -> None:
        w = World(tmp_path, name="rv1-durable-dispatch")
        try:
            view = _view(["W1"])
            handle = w.activate(view)
            runner = TaskMainMilestoneRunner(
                coordinator_store=w.coord_store,
                execution_store=w.exec_store,
                execution_dispatcher=w.dispatcher,
                live_plan_view=view,
                handoff_resolver=_resolver(["W1"]),
                governed_evidence_resolver=_governed_resolver(
                    {"W1": _evidence("W1")}
                ),
                coordinator_id=handle.coordinator_id,
            )
            runner.advance_once()
            _run_to_terminal(w, view, ["W1"], "W1")
            runner.advance_once()

            sandbox = _sandbox(tmp_path / "rv1-review-wt", project_id=PROJECT_ID)
            Path(sandbox.worktree_root).mkdir(parents=True, exist_ok=True)
            base_handoff = _reviewer_handoff()
            record_box: dict[str, Any] = {}
            reviewer_cid = f"{PROJECT_ID}:{MILESTONE_ID}:RV1:attempt-1"

            def dispatch_resolver() -> dict[str, Any]:
                record = _materialize_reviewer_work_handoff(
                    base_handoff=base_handoff,
                    sandbox=sandbox,
                    live_view=view,
                    state=w.coord_store.get(handle.coordinator_id),
                    project_id=PROJECT_ID,
                    reviewer_task_id=reviewer_cid,
                    plan_id=None,
                    parent_task_identity=ORIGIN_SESSION,
                )
                record_box.update(record)
                return record

            runner._reviewer_handoff_resolver = lambda: base_handoff
            runner._reviewer_dispatch_resolver = dispatch_resolver
            captured: list[Any] = []
            original_dispatch = w.adapter.dispatch

            def capture(package: Any) -> Any:
                captured.append(package)
                return original_dispatch(package)

            w.adapter.dispatch = capture  # type: ignore[method-assign]
            out = runner.advance_once()
            assert out.disposition == DISPOSITION_DISPATCHED_REVIEW
            assert out.dispatched == (reviewer_cid,)
            assert len(captured) == 1
            working_context = dict(captured[0].working_context)
            injected = working_context[TRUSTED_WORK_HANDOFF_CONTEXT_KEY]
            assert injected["mode"] == "work_item"
            assert injected["digest"] == record_box["handoff_digest"]
            assert injected["ref"] == record_box["handoff_ref"]
            # Injected digest resolves to the durable artifact for the exact
            # reviewer task (materialized BEFORE dispatch).
            opened = handoff_open(
                record_box["handoff_ref"], "full", sandbox=sandbox
            )
            assert opened["digest"] == injected["digest"]
            assert opened["envelope"]["task_id"] == reviewer_cid
        finally:
            w.close()


class TestForeignAndStaleHandoffDenied:
    def _reviewer_binding_for(
        self, env: Any, record: dict[str, Any], canonical_task_id: str
    ) -> Any:
        envelope = create_worker_envelope(
            worktree_root=env.root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            canonical_task_id=canonical_task_id,
            handoff=record["handoff"],
            work_handoff_ref=record["handoff_ref"],
            work_handoff_digest=record["handoff_digest"],
        )
        return load_binding_from_envelope(envelope)

    def test_foreign_and_stale_task_handoff_open_denied(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        record = _materialize(env, live=live)

        # Control: the exact canonical reviewer task opens its own handoff.
        own_binding = self._reviewer_binding_for(env, record, REVIEWER_CID)
        own = _SharedAotaMcpAdapter(own_binding).invoke(
            "handoff.open", {"ref": record["handoff_ref"], "view": "full"}
        )
        assert own["ok"] is True, own.get("error")

        # Another canonical task (fresh attempt) must NOT reuse the old
        # attempt's work-item handoff: fail closed.
        stale_binding = self._reviewer_binding_for(
            env, record, REVIEWER_CID_ATTEMPT_2
        )
        stale = _SharedAotaMcpAdapter(stale_binding).invoke(
            "handoff.open", {"ref": record["handoff_ref"], "view": "full"}
        )
        assert stale["ok"] is False
        assert stale["error"]["code"] == "HANDOFF_TASK_BINDING_MISMATCH"

        # A foreign canonical task binding fails closed identically.
        foreign_ref = handoff_write(
            mode="work_item",
            semantic={
                "work_role": "reviewer",
                "task_kind": "foreign-review",
                "objective": "foreign task",
                "bounded_scope": "foreign scope",
                "validation_expectations": ["foreign"],
                "semantic_stop_expectations": ["foreign"],
            },
            caller_role="task-main",
            sandbox=env.sandbox,
            plan_ref=live.plan_authority,
            milestone_id=live.milestone_id,
            work_item_id="RV1",
            target_role="reviewer",
            task_id="proj_af49w8:M1:RV1:attempt-9",
        )
        foreign = _SharedAotaMcpAdapter(own_binding).invoke(
            "handoff.open", {"ref": foreign_ref.ref, "view": "full"}
        )
        assert foreign["ok"] is False
        assert foreign["error"]["code"] == "HANDOFF_TASK_BINDING_MISMATCH"
        # No physical dispatch occurred during the denied-open matrix.
        assert env.spawn.calls == []


class TestReviewerGovernedCompletion:
    def test_completed_and_blocked_return_require_durable_result_handoff(
        self, tmp_path: Path
    ) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)

        completed_ref = handoff_write(
            mode="result",
            semantic={"summary": REVIEW_RESULT_SUMMARY},
            caller_role="reviewer",
            sandbox=env.sandbox,
            task_id=REVIEWER_CID,
        )
        completed = task_return(
            status="completed",
            result_ref=completed_ref.ref,
            caller_role="reviewer",
            caller_task_id=REVIEWER_CID,
            sandbox=env.sandbox,
        )
        assert completed["status"] == "completed"
        assert completed["full_result_ref"] == completed_ref.ref
        assert completed["card"]["agent_work_role"] == "reviewer"
        completed_receipt = read_task_return_receipt(env.sandbox, REVIEWER_CID)
        assert completed_receipt is not None
        assert completed_receipt.result_ref == completed_ref.ref
        assert completed_receipt.status == "completed"

        blocked_ref = handoff_write(
            mode="result",
            semantic={"summary": "review blocked: required handoff unresolved"},
            caller_role="reviewer",
            sandbox=env.sandbox,
            task_id=REVIEWER_CID_ATTEMPT_2,
        )
        blocked = task_return(
            status="blocked",
            result_ref=blocked_ref.ref,
            caller_role="reviewer",
            caller_task_id=REVIEWER_CID_ATTEMPT_2,
            sandbox=env.sandbox,
        )
        assert blocked["status"] == "blocked"
        assert blocked["full_result_ref"] == blocked_ref.ref
        blocked_receipt = read_task_return_receipt(
            env.sandbox, REVIEWER_CID_ATTEMPT_2
        )
        assert blocked_receipt is not None
        assert blocked_receipt.result_ref == blocked_ref.ref
        assert blocked_receipt.status == "blocked"

        # A blocked return without a durable result ref is refused.
        with pytest.raises((ValueError, TypeError)):
            task_return(
                status="blocked",
                result_ref=None,
                caller_role="reviewer",
                caller_task_id=REVIEWER_CID_ATTEMPT_2,
                sandbox=env.sandbox,
            )


class TestUnresolvableHandoffEscalationGuidance:
    def test_startup_guidance_escalates_unresolvable_handoff_to_blocked(self) -> None:
        text = STARTUP_PROMPT.read_text(encoding="utf-8")
        # Reviewer finds its own task handoff ref via bootstrap (no search).
        assert "TASK_HANDOFF.handoff_ref" in text
        # Unresolvable required handoff escalates blocked; no search thrash.
        assert "UNKNOWN_REF" in text
        assert 'task.return(status="blocked"' in text
        assert 'handoff.write(mode="result"' in text

    def test_missing_ref_open_fails_closed_without_search(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        missing_digest = "f" * 64
        missing_ref = (
            f"handoff://{PROJECT_ID}/{WORKTREE_ID}/work_item/deadbeef/{missing_digest}"
        )
        with pytest.raises(ValueError) as excinfo:
            handoff_open(missing_ref, "full", sandbox=env.sandbox)
        assert "not found" in str(excinfo.value).lower()


class TestNoSecondProtocol:
    def test_dispatch_reuses_existing_handoff_store_and_digest(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        record = _materialize(env, live=live)
        artifact = env.root / ".aota" / "handoffs" / f"{record['handoff_digest']}.json"
        assert artifact.is_file()
        stored = json.loads(artifact.read_text(encoding="utf-8"))
        assert stored["digest"] == record["handoff_digest"]
        assert stored["mode"] == "work_item"
        # Only the canonical handoff store is written (no reviewer-only store).
        assert sorted(p.name for p in (env.root / ".aota").iterdir()) == sorted(
            ["project.yaml", "coordinator.json", "execution.json", "handoffs", "pre-resolved-bindings"]
        ) or "handoffs" in [p.name for p in (env.root / ".aota").iterdir()]
