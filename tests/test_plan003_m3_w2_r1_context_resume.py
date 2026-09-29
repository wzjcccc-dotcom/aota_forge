"""Plan #003 M3/W2-R1 durable interactive context resume proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError,
    DurableTaskMainContextBinding,
    FileBackedTaskMainContextBindingProjection,
    context_binding_projection_path,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore

from test_plan003_m2_w2_same_session_bind import (
    _add_plan,
    _adapter,
    _payload,
    _world,
)


PLAN_ID = "plan_alpha"
SESSION = "ses_m3_w2_r1_exact"


def _bind(world: dict[str, Path | str], *, origin: str = SESSION):
    _add_plan(world, PLAN_ID)
    adapter = _adapter(world, origin=origin)
    result = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )
    assert result["ok"] is True, result
    assert _payload(adapter, result)["state"] == "bound"
    return adapter


def _inspect(adapter):
    result = adapter.invoke("task_main.context", {"mode": "inspect"})
    if result.get("payload") is not None or not result.get("ok"):
        return result
    hydration = result.get("hydration") or {}
    hydrated = adapter.invoke("result.hydrate", hydration.get("arguments") or {})
    payload = hydrated.get("payload") or {}
    content = payload.get("content") if isinstance(payload, dict) else None
    return {
        "ok": hydrated.get("ok"),
        "payload": json.loads(content) if isinstance(content, str) else payload,
    }


def _projection_path(world: dict[str, Path | str]) -> Path:
    return context_binding_projection_path(Path(world["project_root"]))


def test_adapter_recreate_continuity_uses_exact_session(tmp_path: Path) -> None:
    world = _world(tmp_path)
    first = _bind(world)
    projection_path = _projection_path(world)
    assert projection_path.is_file()
    assert FileBackedTaskMainContextBindingProjection(projection_path).get_for_session(SESSION)

    second = _adapter(world, origin=SESSION)
    assert id(second) != id(first)
    assert second._bound_delegate is not None
    readback = _inspect(second)
    assert readback["ok"] is True
    assert readback["payload"]["state"] == "bound"
    assert readback["payload"]["context"]["project_id"] == "project-a"
    assert readback["payload"]["context"]["plan_id"] == PLAN_ID


def test_process_like_restart_reopens_projection_before_resume(tmp_path: Path) -> None:
    world = _world(tmp_path)
    first = _bind(world)
    projection_path = _projection_path(world)
    first_projection = FileBackedTaskMainContextBindingProjection(projection_path)
    durable = first_projection.get_for_session(SESSION)
    assert durable is not None
    del first

    reopened_projection = FileBackedTaskMainContextBindingProjection(projection_path)
    assert reopened_projection.get_for_session(SESSION).binding_digest == durable.binding_digest
    second = _adapter(world, origin=SESSION)
    assert _inspect(second)["payload"]["state"] == "bound"


def test_resumed_bound_context_can_use_governance_and_authorized_root(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    resumed = _adapter(world, origin=SESSION)
    assert _inspect(resumed)["payload"]["state"] == "bound"

    governance = resumed.invoke(
        "governance.plan",
        {"mode": "inspect", "project_id": "project-a", "plan_id": PLAN_ID},
    )
    assert governance["ok"] is True, governance
    assert PLAN_ID in json.dumps(_payload(resumed, governance), sort_keys=True)

    search = resumed.invoke(
        "workspace.search",
        {"query": "W2 local Plan fixture", "root_ref": "local-governance", "max_results": 5},
    )
    assert search["ok"] is True, search
    read = resumed.invoke(
        "workspace.read",
        {"root_ref": "local-governance", "path": f"{PLAN_ID}/plan.md", "max_bytes": 2000},
    )
    assert read["ok"] is True, read
    denied = resumed.invoke(
        "workspace.read",
        {"root_ref": "foreign-root", "path": "plan.md", "max_bytes": 200},
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] in {
        "AUTHORIZED_ROOT_UNKNOWN",
        "AUTHORITY_DENIED",
        "AUTHORIZED_ROOT_CAPABILITY_DENIED",
    }
    assert _inspect(resumed)["payload"]["state"] == "bound"


def test_same_record_different_session_never_rehydrates(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    other = _adapter(world, origin="ses_m3_w2_r1_other")
    readback = _inspect(other)
    assert readback["ok"] is True
    assert readback["payload"]["state"] == "unbound"
    assert other._bound_delegate is None


def test_project_fingerprint_drift_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    manifest = Path(world["project_root"]) / ".aota" / "project.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("name: project-a", "name: changed"), encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert result["error"]["code"] == "PROJECT_CONTEXT_MISMATCH"
    assert resumed._bound_delegate is None


def test_plan_digest_drift_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    plan_path = Path(world["governance_base"]) / "project-a" / PLAN_ID / "plan.md"
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert result["error"]["code"] in {
        "PLAN_AUTHORITY_SOURCE_UNAVAILABLE",
        "STALE_CONTEXT",
        "AUTHORITY_RESOLUTION_FAILED",
    }
    assert resumed._bound_delegate is None


def test_authority_ref_drift_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    store = SQLiteProjectGovernanceStore(world["store"])
    try:
        record = store.get_plan("project-a", PLAN_ID)
        assert record is not None
        changed = PlanAuthorityBinding(
            plan_id=record.authority.plan_id,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref="local-governance:project-a/foreign-authority",
            source_revision=record.authority.source_revision,
            source_digest=record.authority.source_digest,
        )
        store.compare_and_swap_plan(
            "project-a",
            PLAN_ID,
            record.revision,
            authority=changed,
        )
    finally:
        store.close()

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert result["error"]["code"] in {
        "PLAN_AUTHORITY_COMPOSITION_INVALID",
        "PLAN_AUTHORITY_SOURCE_UNAVAILABLE",
        "AUTHORITY_RESOLUTION_FAILED",
    }
    assert resumed._bound_delegate is None


def test_authorized_root_digest_drift_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    projection_path = _projection_path(world)
    raw = json.loads(projection_path.read_text(encoding="utf-8"))
    original = DurableTaskMainContextBinding.from_dict(raw["records"][0])
    tampered = DurableTaskMainContextBinding(
        project_id=original.project_id,
        project_fingerprint=original.project_fingerprint,
        plan_binding=original.plan_binding,
        origin_task_main_session_ref=original.origin_task_main_session_ref,
        authorized_roots_digest="0" * 64,
    )
    raw["records"][0] = tampered.to_dict()
    projection_path.write_text(json.dumps(raw), encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert result["error"]["code"] == "AUTHORITY_DENIED"
    assert resumed._bound_delegate is None


def test_corrupt_projection_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    _projection_path(world).write_text("{not-json", encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert result["error"]["code"] == "CONTEXT_BINDING_CORRUPT"
    assert resumed._bound_delegate is None


def test_conflicting_exact_session_records_fail_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    projection_path = _projection_path(world)
    raw = json.loads(projection_path.read_text(encoding="utf-8"))
    raw["records"].append(dict(raw["records"][0]))
    projection_path.write_text(json.dumps(raw), encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert result["error"]["code"] == "CONTEXT_BINDING_CONFLICT"
    assert resumed._bound_delegate is None


def test_tampered_evidence_cannot_grant_foreign_project(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    projection_path = _projection_path(world)
    raw = json.loads(projection_path.read_text(encoding="utf-8"))
    original = DurableTaskMainContextBinding.from_dict(raw["records"][0])
    tampered = DurableTaskMainContextBinding(
        project_id="project-b",
        project_fingerprint=original.project_fingerprint,
        plan_binding=original.plan_binding,
        origin_task_main_session_ref=original.origin_task_main_session_ref,
        authorized_roots_digest=original.authorized_roots_digest,
    )
    raw["records"][0] = tampered.to_dict()
    projection_path.write_text(json.dumps(raw), encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert result["ok"] is False
    assert resumed._bound_delegate is None
    assert result["error"]["code"] in {
        "PROJECT_NOT_FOUND",
        "PROJECT_CONTEXT_MISMATCH",
        "AUTHORITY_RESOLUTION_FAILED",
    }


def test_no_projection_record_remains_normal_unbound(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)
    adapter = _adapter(world, origin="ses_m3_w2_r1_no_record")
    result = _inspect(adapter)
    assert result["ok"] is True
    assert result["payload"]["state"] == "unbound"
    assert adapter._bound_delegate is None


def test_durability_failure_rolls_back_bound_delegate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = _world(tmp_path)
    _add_plan(world, PLAN_ID)

    def fail_persist(self, records):
        raise ContextBindingError(
            "CONTEXT_BINDING_PERSISTENCE_FAILED",
            "injected durable write failure",
        )

    monkeypatch.setattr(FileBackedTaskMainContextBindingProjection, "_persist", fail_persist)
    adapter = _adapter(world, origin="ses_m3_w2_r1_persist_failure")
    result = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": PLAN_ID,
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "CONTEXT_BINDING_PERSISTENCE_FAILED"
    assert adapter._bound_delegate is None
    assert not _projection_path(world).exists()
