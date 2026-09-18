"""AF #57 M3/W3 guard: partial finalization re-enters the same replay."""

from pathlib import Path

from aota_forge.composition.stewardship import (
    StewardshipExecutionState,
    create_legacy_stewardship_executor,
)
from aota_forge.runtime.config import TASK_MAIN_RUNTIME_PATH_LEGACY
from tests.test_af57_m3_w3_steward_production_composition import (
    _PartialThenCompleteFinalizer,
    _checkpoint,
    _executor,
    _receipt_for,
    _sandbox,
    _steward_result,
)


def test_partial_finalization_reenters_without_semantic_redispatch(tmp_path: Path) -> None:
    result = _steward_result()
    probe, governance, coordinator, live_view = _executor(tmp_path, lambda _handoff: result)
    partial = _receipt_for(probe, result, final_status="PARTIAL", receipt_id="partial-w3")
    completed = _receipt_for(probe, result, final_status="APPLIED", receipt_id="complete-w3")
    finalizer = _PartialThenCompleteFinalizer(partial, completed)
    dispatch_calls: list[object] = []

    def dispatch(handoff):
        dispatch_calls.append(handoff)
        return result

    executor = create_legacy_stewardship_executor(
        runtime_path=TASK_MAIN_RUNTIME_PATH_LEGACY,
        governance_store=governance,
        coordinator_store=coordinator,
        coordinator_id="aota_forge:M3",
        live_plan_view=live_view,
        finalizer=finalizer,
        semantic_dispatch=dispatch,
        result_sandbox=_sandbox(tmp_path),
        origin_session_ref="session:w3",
    )

    first = executor.execute(_checkpoint(semantic=True))
    second = executor.execute(_checkpoint(semantic=True))

    assert first.execution_state is StewardshipExecutionState.IN_FLIGHT
    assert first.replay is not None
    assert second.execution_state is StewardshipExecutionState.FINALIZED
    assert second.replay is not None
    assert second.replay.lineage_id == first.replay.lineage_id
    assert len(dispatch_calls) == 1
    assert finalizer.calls == 2
    governance.close()
