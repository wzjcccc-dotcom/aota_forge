"""AF #57 M3/W3 guard: production task-main owns the Steward composition."""

from pathlib import Path

import pytest

from aota_forge.composition.stewardship import DurableStewardResultOwner
from aota_forge.composition.task_main import create_task_main_runner
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
import aota_forge.runtime.task_main.runner as runner_module
from aota_forge.runtime.task_main.runner import (
    DISPOSITION_MILESTONE_CLOSURE_READY,
    RunnerOutcome,
)
from aota_forge.work_plane.task_return_receipt import read_task_return_receipt
from tests.test_af57_m3_w3_steward_production_composition import (
    MILESTONE,
    _checkpoint,
    _executor,
    _sandbox,
    _steward_result,
)


def test_production_caller_uses_composition_for_deterministic_and_semantic_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []

    def dispatch(handoff):
        calls.append(handoff)
        return _steward_result()

    probe, governance, coordinator, live_view = _executor(tmp_path, dispatch)
    sandbox = _sandbox(tmp_path)

    def closure_ready(self, *, session_available=True):
        return RunnerOutcome(
            disposition=DISPOSITION_MILESTONE_CLOSURE_READY,
            coordinator_id=self.coordinator_id,
            coordinator_revision=1,
            milestone_closure_ready=True,
        )

    monkeypatch.setattr(runner_module.TaskMainMilestoneRunner, "advance_once", closure_ready)
    composed = create_task_main_runner(
        coordinator_store=coordinator,
        execution_store=InMemoryExecutionStateStore(),
        execution_dispatcher=ExecutionDispatcher(ExecutorRegistry()),
        live_plan_view=live_view,
        handoff_resolver=lambda _ref: pytest.fail("legacy runner handoff path was unexpectedly used"),
        coordinator_id=f"aota_forge:{MILESTONE}",
        stewardship_checkpoint=_checkpoint(semantic=False),
        stewardship_dispatch=dispatch,
        stewardship_sandbox=sandbox,
        stewardship_finalizer=probe.finalizer,
        governance_store=governance,
    )

    deterministic = composed.advance_once()
    assert deterministic.milestone_closure_ready is True
    assert calls == []

    composed.update_stewardship_checkpoint(_checkpoint(semantic=True, checkpoint_id="cp-w3-semantic"))
    semantic = composed.advance_once()
    assert semantic.milestone_closure_ready is True
    assert composed.stewardship_outcome is not None
    assert composed.stewardship_outcome.steward_result == _steward_result()
    assert composed.stewardship_outcome.replay is not None
    task_id = DurableStewardResultOwner.default_task_id(composed.stewardship_outcome.replay)
    assert read_task_return_receipt(sandbox, task_id) is not None
    assert len(calls) == 1
    governance.close()
