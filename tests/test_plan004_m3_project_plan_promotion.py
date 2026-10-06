"""Plan #004 M3 project-bound to same-Project Plan promotion proofs."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError,
    FileBackedTaskMainContextBindingProjection,
    context_binding_projection_path,
)

from test_plan003_m2_w2_same_session_bind import (
    _add_plan,
    _adapter,
    _inspect,
    _payload,
    _world,
)


PLAN_ID = "plan_alpha"
SESSION = "ses_plan004_promotion_exact"


def _project_bound(world: dict[str, Path | str], *, origin: str = SESSION):
    adapter = _adapter(world, origin=origin)
    result = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a"},
    )
    assert result["ok"] is True, result
    assert _payload(adapter, result)["state"] == "project_bound"
    return adapter


def test_project_bound_promotes_same_session_to_same_project_plan(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _project_bound(world)
    adapter_id = id(adapter)

    promoted = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )

    assert promoted["ok"] is True, promoted
    assert id(adapter) == adapter_id
    assert _payload(adapter, promoted)["state"] == "bound"
    inspected = _inspect(adapter)["payload"]
    assert inspected["state"] == "bound"
    assert inspected["origin_session_ref"] == SESSION
    assert inspected["context"]["project_id"] == "project-a"
    assert inspected["context"]["plan_id"] == PLAN_ID
    assert inspected["context"]["authority_binding"]["source_digest"]

    projection = FileBackedTaskMainContextBindingProjection(
        context_binding_projection_path(Path(world["project_root"]))
    )
    durable = projection.get_for_session(SESSION)
    assert durable is not None
    assert durable.plan_binding.plan_id == PLAN_ID


def test_project_bound_plan_promotion_can_use_current_project_as_trusted_scope(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _project_bound(world, origin="ses_plan004_promotion_implicit_project")

    promoted = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "plan_selector": PLAN_ID},
    )

    assert promoted["ok"] is True, promoted
    assert _inspect(adapter)["payload"]["state"] == "bound"


def test_project_bound_foreign_plan_is_denied_and_context_remains_project_only(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    _add_plan(world, "plan_foreign", project_id="project-b")
    adapter = _project_bound(world, origin="ses_plan004_foreign_plan")

    denied = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": "plan_foreign",
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "PLAN_NOT_FOUND"
    assert _inspect(adapter)["payload"]["state"] == "project_bound"


def test_project_bound_foreign_project_plan_is_denied_by_existing_context_guard(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_foreign", project_id="project-b")
    adapter = _project_bound(world, origin="ses_plan004_foreign_project")

    denied = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-b",
            "plan_selector": "plan_foreign",
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "PROJECT_CONTEXT_MISMATCH"
    assert _inspect(adapter)["payload"]["state"] == "project_bound"


def test_project_bound_stale_plan_authority_is_denied(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _project_bound(world, origin="ses_plan004_stale_authority")
    plan_path = Path(world["governance_base"]) / "project-a" / PLAN_ID / "plan.md"
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "STALE=yes\n", encoding="utf-8")

    denied = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "PLAN_AUTHORITY_SOURCE_UNAVAILABLE"
    assert _inspect(adapter)["payload"]["state"] == "project_bound"


def test_project_bound_invalid_plan_locator_is_denied(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _project_bound(world, origin="ses_plan004_invalid_locator")

    denied = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": "/tmp/plan.md",
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "PLAN_SELECTOR_INVALID"
    assert _inspect(adapter)["payload"]["state"] == "project_bound"


def test_promotion_persistence_failure_rolls_back_to_project_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _project_bound(world, origin="ses_plan004_promotion_persist_failure")

    def fail_persist(self, records):
        raise ContextBindingError(
            "CONTEXT_BINDING_PERSISTENCE_FAILED",
            "injected promotion durable write failure",
        )

    monkeypatch.setattr(FileBackedTaskMainContextBindingProjection, "_persist", fail_persist)
    denied = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "CONTEXT_BINDING_PERSISTENCE_FAILED"
    assert adapter._bound_delegate is not None
    assert _inspect(adapter)["payload"]["state"] == "project_bound"
    durable = FileBackedTaskMainContextBindingProjection(
        context_binding_projection_path(Path(world["project_root"]))
    ).get_for_session("ses_plan004_promotion_persist_failure")
    assert durable.binding_kind == "PROJECT_BOUND"
    assert durable.plan_binding is None


def test_promotion_post_write_failure_removes_durable_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _project_bound(world, origin="ses_plan004_promotion_post_write_failure")
    original_persist = FileBackedTaskMainContextBindingProjection._persist

    def persist_then_fail(self, records):
        original_persist(self, records)
        raise ContextBindingError(
            "CONTEXT_BINDING_PERSISTENCE_FAILED",
            "injected post-write promotion failure",
        )

    monkeypatch.setattr(
        FileBackedTaskMainContextBindingProjection,
        "_persist",
        persist_then_fail,
    )
    denied = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )

    assert denied["ok"] is False
    assert denied["error"]["code"] == "CONTEXT_BINDING_PERSISTENCE_FAILED"
    assert _inspect(adapter)["payload"]["state"] == "project_bound"
    projection = FileBackedTaskMainContextBindingProjection(
        context_binding_projection_path(Path(world["project_root"]))
    )
    durable = projection.get_for_session("ses_plan004_promotion_post_write_failure")
    assert durable.binding_kind == "PROJECT_BOUND"
    assert durable.plan_binding is None


def test_promotion_restarts_as_plan_bound_context(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _project_bound(world, origin="ses_plan004_promotion_restart")
    promoted = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )
    assert promoted["ok"] is True, promoted
    adapter.close()

    resumed = _adapter(world, origin="ses_plan004_promotion_restart")
    inspected = _inspect(resumed)["payload"]
    assert inspected["state"] == "bound"
    assert inspected["context"]["project_id"] == "project-a"
    assert inspected["context"]["plan_id"] == PLAN_ID


def test_two_session_promotion_is_isolated(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    session_a = _project_bound(world, origin="ses_plan004_A")
    session_b = _project_bound(world, origin="ses_plan004_B")

    promoted = session_a.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )

    assert promoted["ok"] is True, promoted
    assert _inspect(session_a)["payload"]["state"] == "bound"
    assert _inspect(session_b)["payload"]["state"] == "project_bound"
    assert session_a._bound_delegate is not session_b._bound_delegate


def test_shared_router_concurrent_promotion_keeps_session_contexts_separate(
    tmp_path: Path,
) -> None:
    from aota_forge.mcp_transport import SessionScopedContextRouter, UnboundHostContext
    from aota_forge.session_bridge import BRIDGE_CARRIER_KEY
    from test_plan004_m2_bridge import _bridge_process

    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    with _bridge_process(tmp_path) as client:
        router = SessionScopedContextRouter(
            UnboundHostContext(
                repo_root=str(Path(__file__).resolve().parents[1]),
                registry_path=str(world["registry"]),
                runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
                governance_base=str(world["governance_base"]),
                governance_store_path=str(world["store"]),
                bridge_socket_path=client.socket_path,
                bind_global_dispatcher=False,
            )
        )

        def call(session: str, call_id: str, arguments: dict[str, str]):
            token = client.issue(session, call_id, "task_main.context", arguments)
            return router.invoke(
                "task_main.context",
                {
                    **arguments,
                    BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": token},
                },
            )

        for session, call_id in (("ses_plan004_router_A", "bind-project-A"), ("ses_plan004_router_B", "bind-project-B")):
            result = call(session, call_id, {"mode": "bind", "project_selector": "project-a"})
            assert result["ok"] is True, result

        def promote(item: tuple[str, str]):
            session, call_id = item
            return call(
                session,
                call_id,
                {
                    "mode": "bind",
                    "project_selector": "project-a",
                    "plan_selector": PLAN_ID,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    promote,
                    (("ses_plan004_router_A", "promote-A"), ("ses_plan004_router_B", "promote-B")),
                )
            )
        assert all(result["ok"] for result in results), results

        inspected = {
            session: call(session, f"inspect-{session}", {"mode": "inspect"})
            for session in ("ses_plan004_router_A", "ses_plan004_router_B")
        }
        assert all(result["ok"] for result in inspected.values())
        assert all(_router_payload(router, result)["state"] == "bound" for result in inspected.values())
        assert router._slots["ses_plan004_router_A"].adapter._bound_delegate is not router._slots[
            "ses_plan004_router_B"
        ].adapter._bound_delegate
        router.close()


def _router_payload(router, result: dict) -> dict:
    """Decode inline or by-ref context output for the router proof."""
    if isinstance(result.get("payload"), dict):
        return result["payload"]
    hydration = result.get("hydration") or {}
    arguments = hydration.get("arguments") if isinstance(hydration, dict) else None
    assert isinstance(arguments, dict)
    # The isolation assertion only needs the bound adapter's direct inspect
    # payload; the router's result is expected to be inline for this fixture.
    hydrated = router._slots[next(iter(router._slots))].adapter.invoke(
        "result.hydrate", arguments
    )
    payload = hydrated.get("payload") or {}
    content = payload.get("content") if isinstance(payload, dict) else None
    return json.loads(content) if isinstance(content, str) else payload
