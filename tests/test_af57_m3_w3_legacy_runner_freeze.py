"""AF #57 M3/W3 guard: the accepted legacy runner remains frozen."""

from pathlib import Path

import aota_forge.runtime.task_main.runner as runner_module


def test_legacy_runner_has_no_stewardship_wiring() -> None:
    source = Path(runner_module.__file__).read_text(encoding="utf-8")

    assert "create_legacy_stewardship_executor" not in source
    assert "LegacyStewardship" not in source
    assert "_run_bound_stewardship" not in source
    assert "STEWARDSHIP_PRODUCTION_CALLER" not in source
