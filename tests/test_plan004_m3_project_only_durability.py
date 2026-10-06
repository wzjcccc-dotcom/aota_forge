"""Bounded Project-only durability, fail-closed recovery and v1 compatibility."""
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError, DurableTaskMainContextBinding,
    FileBackedTaskMainContextBindingProjection, context_binding_projection_path,
)
from test_plan003_m2_w2_same_session_bind import _adapter, _world, _add_plan, _payload
from test_plan003_m3_w2_r1_context_resume import _inspect

SESSION = "ses_plan004_project_durable_A"

def _bind(world):
    adapter = _adapter(world, origin=SESSION)
    assert _inspect(adapter)["payload"]["state"] == "unbound"
    result = adapter.invoke("task_main.context", {"mode": "bind", "project_selector": "project-a"})
    assert result["ok"], result
    assert _payload(adapter, result)["state"] == "project_bound"
    return adapter

def _projection(world):
    return FileBackedTaskMainContextBindingProjection(context_binding_projection_path(world["project_root"]))

def test_v1_golden_plan_record_preserves_semantics_serialization_and_digest():
    raw = json.loads((Path(__file__).parent / "fixtures/plan004-plan-bound-v1-golden.json").read_text())
    binding = DurableTaskMainContextBinding.from_dict(raw)
    assert binding.binding_kind == "PLAN_BOUND"
    assert binding.plan_binding.plan_id == "plan_alpha"
    assert binding.plan_binding.source_digest == "b" * 64
    assert binding.compute_digest() == raw["binding_digest"]
    assert binding.to_dict() == raw

@pytest.mark.parametrize("version", [1, 2])
def test_plan_bound_evidence_still_requires_plan_and_source_digest(version):
    from aota_forge.adapters.plan_authority.binding import PlanAuthorityBinding
    raw = json.loads((Path(__file__).parent / "fixtures/plan004-plan-bound-v1-golden.json").read_text())
    plan = dict(raw["plan_binding"])
    plan["source_digest"] = None
    with pytest.raises(ContextBindingError, match="source digest"):
        DurableTaskMainContextBinding(
            project_id="project-a", project_fingerprint="a" * 64,
            plan_binding=PlanAuthorityBinding.from_dict(plan),
            origin_task_main_session_ref=SESSION, schema_version=version,
        )
    raw["schema_version"] = version
    if version == 2:
        raw["binding_kind"] = "PLAN_BOUND"
    raw.pop("plan_binding")
    with pytest.raises(ContextBindingError):
        DurableTaskMainContextBinding.from_dict(raw)
    with pytest.raises(ContextBindingError):
        DurableTaskMainContextBinding(
            project_id="project-a", project_fingerprint="a" * 64, plan_binding=None,
            origin_task_main_session_ref=SESSION, schema_version=version,
        )

def test_project_v2_requires_explicit_discriminator(tmp_path):
    world = _world(tmp_path)
    _bind(world).close()
    raw = _projection(world).get_for_session(SESSION).to_dict()
    raw.pop("binding_kind")
    with pytest.raises(ContextBindingError):
        DurableTaskMainContextBinding.from_dict(raw)

def test_plan_promotion_denies_incompatible_project_roots_evidence(tmp_path):
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    adapter = _bind(world)
    projection = _projection(world)
    record = projection.get_for_session(SESSION)
    projection._persist([replace(record, authorized_roots_digest="0" * 64, binding_digest="")])
    result = adapter.invoke("task_main.context", {"mode": "bind", "plan_selector": "plan_alpha"})
    assert not result["ok"] and result["error"]["code"] == "AUTHORITY_DENIED"
    assert _inspect(adapter)["payload"]["state"] == "project_bound"
    assert projection.get_for_session(SESSION).binding_kind == "PROJECT_BOUND"

def test_project_persist_restart_and_same_project_plan_promotion(tmp_path):
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    first = _bind(world)
    record = _projection(world).get_for_session(SESSION)
    assert record.binding_kind == "PROJECT_BOUND"
    assert record.schema_version == 2
    assert record.project_id == "project-a"
    assert record.plan_binding is None
    assert "plan_binding" not in record.to_dict()
    assert record.authorized_roots_digest
    first.close()
    resumed = _adapter(world, origin=SESSION)
    assert resumed._resume_error is None
    context = _inspect(resumed)["payload"]
    assert context["state"] == "project_bound"
    assert context["context"]["project_id"] == "project-a"
    assert context["context"]["plan_id"] is None
    promoted = resumed.invoke("task_main.context", {"mode": "bind", "plan_selector": "plan_alpha"})
    assert promoted["ok"], promoted
    assert _projection(world).get_for_session(SESSION).binding_kind == "PLAN_BOUND"
    resumed.close()
    assert _inspect(_adapter(world, origin=SESSION))["payload"]["state"] == "bound"

def test_project_restart_exact_session_isolation(tmp_path):
    world = _world(tmp_path)
    _bind(world).close()
    other = "ses_plan004_project_durable_B"
    assert _projection(world).get_for_session(other) is None
    assert _inspect(_adapter(world, origin=SESSION))["payload"]["state"] == "project_bound"
    assert _inspect(_adapter(world, origin=other))["payload"]["state"] == "unbound"

@pytest.mark.parametrize("failure", ["before_write", "after_write", "readback", "readback_mismatch"])
def test_project_persistence_failure_restores_unbound_and_no_valid_record(tmp_path, monkeypatch, failure):
    world = _world(tmp_path)
    adapter = _adapter(world, origin=SESSION)
    cls = FileBackedTaskMainContextBindingProjection
    original_write, original_read = cls._persist, cls._read
    armed = False
    def write(self, records):
        nonlocal armed
        if records:
            assert adapter._bound_delegate is None
            if failure == "before_write":
                raise ContextBindingError("CONTEXT_BINDING_PERSISTENCE_FAILED", "write failure")
            original_write(self, records)
            armed = True
            if failure == "after_write":
                raise ContextBindingError("CONTEXT_BINDING_PERSISTENCE_FAILED", "post-write failure")
        else:
            original_write(self, records)
    def read(self):
        nonlocal armed
        if armed and failure in ("readback", "readback_mismatch"):
            armed = False
            if failure == "readback":
                raise ContextBindingError("CONTEXT_BINDING_PERSISTENCE_FAILED", "readback failure")
            return []
        return original_read(self)
    monkeypatch.setattr(cls, "_persist", write)
    monkeypatch.setattr(cls, "_read", read)
    result = adapter.invoke("task_main.context", {"mode": "bind", "project_selector": "project-a"})
    assert not result["ok"]
    assert result["error"]["code"] == "CONTEXT_BINDING_PERSISTENCE_FAILED"
    assert adapter._bound_delegate is None
    assert _inspect(adapter)["payload"]["state"] == "unbound"
    assert _projection(world).get_for_session(SESSION) is None

@pytest.mark.parametrize("field,value", [
    ("project_fingerprint", "bad"), ("binding_digest", "0" * 64),
    ("origin_task_main_session_ref", "unbound"), ("binding_kind", "UNKNOWN"),
    ("plan_binding", None), ("authorized_roots_digest", "bad"),
    ("binding_digest", ""),
])
def test_corrupt_project_record_fails_closed(tmp_path, field, value):
    world = _world(tmp_path)
    _bind(world).close()
    projection = _projection(world)
    document = json.loads(projection.path.read_text())
    document["records"][0][field] = value
    projection.path.write_text(json.dumps(document))
    with pytest.raises(ContextBindingError):
        projection.get_for_session(SESSION)
    resumed = _adapter(world, origin=SESSION)
    assert resumed._bound_delegate is None
    assert not _inspect(resumed)["ok"]

@pytest.mark.parametrize("drift", ["project", "roots"])
def test_project_recovery_reresolves_current_identity_and_roots(tmp_path, drift):
    world = _world(tmp_path)
    _bind(world).close()
    if drift == "project":
        path = Path(world["project_root"]) / ".aota/project.yaml"
        path.write_text(path.read_text().replace("name: project-a", "name: changed"))
    else:
        projection = _projection(world)
        record = projection.get_for_session(SESSION)
        projection._persist([replace(record, authorized_roots_digest="0" * 64, binding_digest="")])
    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)
    assert not result["ok"]
    assert result["error"]["code"] == ("PROJECT_CONTEXT_MISMATCH" if drift == "project" else "AUTHORITY_DENIED")
    assert resumed._bound_delegate is None

def test_project_router_restart_with_rotated_bridge_epoch(tmp_path):
    from aota_forge.mcp_transport import SessionScopedContextRouter, UnboundHostContext
    from aota_forge.session_bridge import BRIDGE_CARRIER_KEY
    from test_plan004_m2_bridge import _bridge_process
    world = _world(tmp_path)
    def context(client):
        return UnboundHostContext(
            repo_root=str(Path(__file__).resolve().parents[1]), registry_path=str(world["registry"]),
            runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
            governance_base=str(world["governance_base"]), governance_store_path=str(world["store"]),
            bridge_socket_path=client.socket_path, bind_global_dispatcher=False,
        )
    def call(router, client, session, call_id, args):
        token = client.issue(session, call_id, "task_main.context", args)
        result = router.invoke("task_main.context", {**args, BRIDGE_CARRIER_KEY: {"schema": "aota-bridge-v1", "token": token}})
        assert result["ok"], result
        return result
    first_bridge = tmp_path / "first-bridge"
    second_bridge = tmp_path / "second-bridge"
    first_bridge.mkdir()
    second_bridge.mkdir()
    with _bridge_process(first_bridge) as client:
        old_epoch = client.rotate()
        router = SessionScopedContextRouter(context(client))
        call(router, client, SESSION, "project-A", {"mode": "bind", "project_selector": "project-a"})
        call(router, client, "ses_other", "inspect-B", {"mode": "inspect"})
        router.close()
    with _bridge_process(second_bridge) as client:
        assert client.rotate() != old_epoch
        router = SessionScopedContextRouter(context(client))
        call(router, client, SESSION, "resume-A", {"mode": "inspect"})
        call(router, client, "ses_other", "resume-B", {"mode": "inspect"})
        a = _inspect(router._slots[SESSION].adapter)["payload"]
        b = _inspect(router._slots["ses_other"].adapter)["payload"]
        assert a["state"] == "project_bound" and a["context"]["plan_id"] is None
        assert b["state"] == "unbound"
        assert "token" not in _projection(world).path.read_text()
        router.close()
