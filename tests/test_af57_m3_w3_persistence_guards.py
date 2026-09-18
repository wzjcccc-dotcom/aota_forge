"""AF #57 M3/W3 guard: terminal completion follows durable result evidence."""

from pathlib import Path

from aota_forge.composition.stewardship import DurableStewardResultOwner, StewardshipExecutionState
from aota_forge.work_plane.task_return_receipt import read_task_return_receipt
from tests.test_af57_m3_w3_steward_production_composition import (
    _checkpoint,
    _executor,
    _sandbox,
    _steward_result,
)


def test_completed_requires_durable_result_and_verified_finalizer_receipt(tmp_path: Path) -> None:
    sandbox = _sandbox(tmp_path)
    executor, governance, *_ = _executor(
        tmp_path,
        lambda _handoff: _steward_result(),
        result_sandbox=sandbox,
    )

    outcome = executor.execute(_checkpoint(semantic=True))

    assert outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert outcome.replay is not None
    assert outcome.receipt is not None
    task_id = DurableStewardResultOwner.default_task_id(outcome.replay)
    task_receipt = read_task_return_receipt(sandbox, task_id)
    assert task_receipt is not None
    assert executor.result_owner.resolve_steward_result(outcome.replay) == _steward_result()
    stored_receipt = executor.finalizer.receipt_store.get(outcome.receipt.idempotency_key)
    assert stored_receipt == outcome.receipt
    governance.close()
