"""AF #57 M3/W3 guard: result ownership is durable, bounded, and fail-closed."""

from pathlib import Path

from aota_forge.composition.stewardship import StewardshipExecutionState
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from tests.test_af57_m3_w3_steward_production_composition import (
    _checkpoint,
    _executor,
    _steward_result,
)


def test_fresh_replay_hydrates_result_without_dispatch(tmp_path: Path) -> None:
    first_calls: list[object] = []

    def first_dispatch(handoff):
        first_calls.append(handoff)
        return _steward_result()

    first, governance, coordinator, live_view = _executor(
        tmp_path, first_dispatch
    )
    checkpoint = _checkpoint(semantic=True)
    assert first.execute(checkpoint).execution_state is StewardshipExecutionState.FINALIZED
    governance.close()

    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    second_calls: list[object] = []

    def second_dispatch(handoff):
        second_calls.append(handoff)
        raise AssertionError("completed replay must not redispatch")

    second, reopened, *_ = _executor(
        tmp_path,
        second_dispatch,
        governance_store=reopened,
        coordinator_store=coordinator,
        live_plan_view=live_view,
    )
    outcome = second.execute(checkpoint)

    assert outcome.execution_state is StewardshipExecutionState.FINALIZED
    assert outcome.steward_result == _steward_result()
    assert first_calls and second_calls == []
    reopened.close()


def test_missing_completed_result_fails_closed_without_dispatch(tmp_path: Path) -> None:
    first_calls: list[object] = []

    def first_dispatch(handoff):
        first_calls.append(handoff)
        return _steward_result()

    first, governance, coordinator, live_view = _executor(
        tmp_path, first_dispatch
    )
    checkpoint = _checkpoint(semantic=True)
    assert first.execute(checkpoint).execution_state is StewardshipExecutionState.FINALIZED
    for receipt_path in (tmp_path / ".aota" / "task_return_receipts").glob("*.json"):
        receipt_path.unlink()
    governance.close()

    reopened = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    second_calls: list[object] = []

    def second_dispatch(handoff):
        second_calls.append(handoff)
        raise AssertionError("missing result must not redispatch")

    second, reopened, *_ = _executor(
        tmp_path,
        second_dispatch,
        governance_store=reopened,
        coordinator_store=coordinator,
        live_plan_view=live_view,
    )
    outcome = second.execute(checkpoint)

    assert outcome.execution_state is StewardshipExecutionState.FAILED_CLOSED
    assert outcome.error_code == "COMPLETED_RESULT_UNAVAILABLE"
    assert first_calls and second_calls == []
    reopened.close()


def test_unknown_dispatch_is_failed_closed_and_not_retried(tmp_path: Path) -> None:
    calls: list[object] = []

    def unknown_dispatch(handoff):
        calls.append(handoff)
        raise RuntimeError("transport outcome unknown")

    executor, governance, *_ = _executor(
        tmp_path,
        unknown_dispatch,
    )
    checkpoint = _checkpoint(semantic=True)

    first = executor.execute(checkpoint)
    second = executor.execute(checkpoint)

    assert first.execution_state is StewardshipExecutionState.FAILED_CLOSED
    assert second.execution_state is StewardshipExecutionState.FAILED_CLOSED
    assert first.error_code == "SEMANTIC_DISPATCH_UNKNOWN_OUTCOME"
    assert len(calls) == 1
    governance.close()
