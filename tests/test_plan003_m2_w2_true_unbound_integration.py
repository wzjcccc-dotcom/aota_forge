"""Plan #003 M2/W2 bounded true-unbound integration proof."""

from __future__ import annotations

from pathlib import Path

from test_plan003_m2_w2_same_session_bind import (
    _add_plan,
    _adapter,
    _inspect,
    _payload,
    _world,
)


def test_true_unbound_adapter_binds_and_reads_back_in_same_session(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")

    # This is intentionally a genuinely unbound start.  No trusted binding,
    # PlanAuthorityBinding or runtime context is injected before the first call.
    adapter = _adapter(world, origin="ses_true_unbound")
    assert adapter._bound_delegate is None
    assert adapter._bound_binding is None

    first = _inspect(adapter)
    assert first["ok"] is True
    assert first["payload"]["state"] == "unbound"

    bind = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": "plan_alpha",
        },
    )
    assert bind["ok"] is True
    assert adapter._bound_delegate is not None
    assert adapter._bound_binding is not None
    assert adapter._bound_binding.plan_authority_binding is not None

    second = _inspect(adapter)
    assert second["ok"] is True
    assert second["payload"]["state"] == "bound"
    assert second["payload"]["context"]["project_id"] == "project-a"
    assert second["payload"]["context"]["plan_id"] == "plan_alpha"
    assert second["payload"]["origin_session_ref"] == "ses_true_unbound"
    assert second["payload"]["context"]["authorized_roots"]
    assert _payload(adapter, bind)["context"]["plan_id"] == "plan_alpha"
