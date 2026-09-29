"""Plan #003 M2/W2 same-session bind security and compatibility proofs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import local_plan_authority_reference
from aota_forge.core.task_main_context import dispatch_task_main_context
from aota_forge.core_ingress import CanonicalDispatchBinding
from aota_forge.governance.project_store import PLAN_LIFECYCLE_ACTIVE, ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.mcp_transport import (
    MCP_PUBLIC_TOOL_COUNT,
    UnboundHostContext,
    _SharedAotaMcpAdapter,
    _UnboundAotaAdapter,
)


MANIFEST = """schema_version: 1
project:
  id: {project_id}
  name: {project_name}
  kind: test
  status: active
summary: bounded W2 fixture
capabilities: []
paths:
  source_root: .
  source: []
  docs: []
  scripts: []
  profiles: []
  skills: []
  tests: []
commands:
  validate: []
  deploy: []
  verify_deploy: []
runtime:
  deployment_type: manual
  requires_human_checkpoint: false
codegraph:
  enabled: false
  index_location: .codegraph/
plan:
  active_plan_id: null
constraints: []
"""

PLAN_BODY = """# W2 local Plan fixture

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```
"""


def _write_project(root: Path, project_id: str, name: str | None = None) -> None:
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id, project_name=name or project_id),
        encoding="utf-8",
    )


def _world(tmp_path: Path, *, project_id: str = "project-a") -> dict[str, Path | str]:
    workspace = tmp_path / "workspace"
    project_root = workspace / project_id
    _write_project(project_root, project_id)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"trusted-workspace": {"candidates": [str(workspace)]}}),
        encoding="utf-8",
    )
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    return {
        "workspace": workspace,
        "project_root": project_root,
        "registry": registry,
        "governance_base": governance_base,
        "store": tmp_path / "governance.sqlite3",
        "project_id": project_id,
    }


def _add_plan(
    world: dict[str, Path | str],
    plan_id: str,
    *,
    project_id: str | None = None,
    authority_ref: str | None = None,
) -> None:
    project = project_id or str(world["project_id"])
    plan_dir = Path(world["governance_base"]) / project / plan_id
    plan_dir.mkdir(parents=True)
    plan_path = plan_dir / "plan.md"
    plan_path.write_text(PLAN_BODY, encoding="utf-8")
    digest = hashlib.sha256(PLAN_BODY.encode("utf-8")).hexdigest()
    binding = PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=authority_ref or local_plan_authority_reference(project, plan_id),
        source_revision=digest,
        source_digest=digest,
    )
    store = SQLiteProjectGovernanceStore(world["store"])
    try:
        store.put_plan(
            ProjectPlanRecord(
                project_id=project,
                plan_id=plan_id,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=binding,
            )
        )
    finally:
        store.close()


def _adapter(world: dict[str, Path | str], *, origin: str = "ses_w2_exact") -> _UnboundAotaAdapter:
    return _UnboundAotaAdapter(
        UnboundHostContext(
            repo_root=str(Path(__file__).resolve().parent.parent),
            registry_path=str(world["registry"]),
            runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
            origin_session_ref=origin,
            governance_base=str(world["governance_base"]),
            governance_store_path=str(world["store"]),
        )
    )


def _inspect(adapter: _UnboundAotaAdapter) -> dict:
    result = adapter.invoke("task_main.context", {"mode": "inspect"})
    if result.get("payload") is not None:
        return result
    hydration = result.get("hydration") or {}
    hydrated = adapter.invoke("result.hydrate", hydration.get("arguments") or {})
    payload = hydrated.get("payload") or {}
    content = payload.get("content") if isinstance(payload, dict) else None
    decoded = json.loads(content) if isinstance(content, str) else payload
    return {"ok": hydrated.get("ok"), "payload": decoded, "raw": result}


def _payload(adapter: _UnboundAotaAdapter, result: dict) -> dict:
    if isinstance(result.get("payload"), dict):
        return result["payload"]
    hydration = result.get("hydration") or {}
    hydrated = adapter.invoke("result.hydrate", hydration.get("arguments") or {})
    assert hydrated["ok"] is True
    assert isinstance(hydrated.get("payload"), dict)
    content = hydrated["payload"].get("content")
    return json.loads(content) if isinstance(content, str) else hydrated["payload"]


def test_unbound_inspect_is_truthful_and_missing_session_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world, origin="")
    assert _inspect(adapter)["payload"]["state"] == "unbound"

    result = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "SESSION_RECOVERY_REQUIRED"
    assert adapter._bound_delegate is None


def test_project_only_bind_materializes_cards_route_and_same_adapter(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)
    adapter_id = id(adapter)

    result = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a"},
    )
    assert result["ok"] is True
    assert _payload(adapter, result)["state"] == "project_bound"
    assert id(adapter) == adapter_id
    assert adapter._bound_delegate is not None

    readback = _inspect(adapter)
    assert readback["ok"] is True
    assert readback["payload"]["state"] == "project_bound"
    assert readback["payload"]["context"]["project_id"] == "project-a"
    assert readback["payload"]["context"]["plan_id"] is None
    assert readback["payload"]["origin_session_ref"] == "ses_w2_exact"
    assert {item["root_ref"] for item in readback["payload"]["context"]["authorized_roots"]} == {
        "project-main",
        "active-worktree",
    }
    governance = readback["payload"]["governance_context"]
    assert governance["CARDS_PRESENT"] is True
    assert governance["CARDS"]["project"] is not None
    assert governance["CARDS"]["plans"] == []
    assert governance["CARDS"]["milestones"] == []
    assert governance["CONTEXT_ROUTE"]["plan_id"] is None
    assert not any(
        entry["kind"] == "plan_authority"
        for entry in governance["CONTEXT_ROUTE"]["entries"]
    )


def test_plan_bound_bind_derives_authority_source_and_readback(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    adapter = _adapter(world)

    result = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": "plan_alpha",
        },
    )
    assert result["ok"] is True
    assert _payload(adapter, result)["state"] == "bound"
    readback = _inspect(adapter)
    assert readback["ok"] is True
    context = readback["payload"]["context"]
    assert context["project_id"] == "project-a"
    assert context["plan_id"] == "plan_alpha"
    assert context["authority_binding"]["source_kind"] == "local_governance"
    assert context["authority_binding"]["source_digest"]
    assert any(
        item["root_ref"] == "local-governance"
        for item in context["authorized_roots"]
    )
    route = readback["payload"]["governance_context"]["CONTEXT_ROUTE"]
    assert route["plan_id"] == "plan_alpha"
    assert any(entry["kind"] == "plan_authority" for entry in route["entries"])


def test_unknown_project_and_ambiguous_project_leave_adapter_unbound(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)
    unknown = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "missing"},
    )
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == "PROJECT_NOT_FOUND"
    assert adapter._bound_delegate is None

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_project(first / "one", "project-one", "Shared Project")
    _write_project(second / "two", "project-two", "Shared Project")
    registry = tmp_path / "ambiguous-registry.json"
    registry.write_text(
        json.dumps(
            {
                "workspace-one": {"candidates": [str(first)]},
                "workspace-two": {"candidates": [str(second)]},
            }
        ),
        encoding="utf-8",
    )
    ambiguous = _UnboundAotaAdapter(
        UnboundHostContext(
            repo_root=str(Path(__file__).resolve().parent.parent),
            registry_path=str(registry),
            runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
            origin_session_ref="ses_ambiguous",
        )
    ).invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "Shared Project"},
    )
    assert ambiguous["ok"] is False
    assert ambiguous["error"]["code"] == "PROJECT_AMBIGUOUS"


@pytest.mark.parametrize("plan_selector", ["plan_missing", "local-governance:project-a/plan_other"])
def test_unknown_or_cross_project_plan_fails_closed(tmp_path: Path, plan_selector: str) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    _add_plan(world, "plan_other", project_id="project-b")
    adapter = _adapter(world)
    result = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "plan_selector": plan_selector,
        },
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "PLAN_NOT_FOUND"
    assert adapter._bound_delegate is None


def test_ambiguous_plan_stale_source_and_raw_path_fail_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    shared_ref = "local-governance:project-a/shared-selector"
    _add_plan(world, "plan_alpha", authority_ref=shared_ref)
    _add_plan(world, "plan_beta", authority_ref=shared_ref)
    adapter = _adapter(world)
    ambiguous = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a", "plan_selector": shared_ref},
    )
    assert ambiguous["ok"] is False
    assert ambiguous["error"]["code"] == "PLAN_AMBIGUOUS"

    stale_root = tmp_path / "stale"
    stale_root.mkdir()
    stale_world = _world(stale_root)
    _add_plan(stale_world, "plan_alpha")
    stale_adapter = _adapter(stale_world)
    plan_path = Path(stale_world["governance_base"]) / "project-a" / "plan_alpha" / "plan.md"
    plan_path.write_text(PLAN_BODY + "changed\n", encoding="utf-8")
    stale = stale_adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a", "plan_selector": "plan_alpha"},
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "PLAN_AUTHORITY_SOURCE_UNAVAILABLE"
    raw = stale_adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": str(world["project_root"])},
    )
    assert raw["ok"] is False
    assert raw["error"]["code"] == "PROJECT_SELECTOR_INVALID"
    assert adapter._bound_delegate is None
    assert stale_adapter._bound_delegate is None


def test_selector_alias_conflict_and_model_session_identity_are_rejected(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)
    conflict = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "project_ref": "other-project",
        },
    )
    assert conflict["ok"] is False
    assert conflict["error"]["code"] == "INVALID_INPUT"
    forged = adapter.invoke(
        "task_main.context",
        {
            "mode": "bind",
            "project_selector": "project-a",
            "session_id": "model-forged-session",
        },
    )
    assert forged["ok"] is False
    assert forged["error"]["code"] == "UNKNOWN_INPUT"
    assert adapter._bound_delegate is None


def test_invalid_authorized_root_set_fails_closed() -> None:
    binding = SimpleNamespace(
        project_id="project-a",
        plan_authority_binding=PlanAuthorityBinding(
            plan_id="plan_alpha",
            source_kind="local_governance",
            authority_ref="local-governance:project-a/plan_alpha",
            source_revision="r1",
            source_digest="a" * 64,
        ),
        sandbox=object(),
        effective_authorized_roots=lambda: object(),
    )
    response = dispatch_task_main_context(
        {"mode": "inspect"},
        binding,
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error["code"] == "AUTHORITY_DENIED"


def test_project_only_plan_operations_and_switch_remain_fail_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)
    bound = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a"},
    )
    assert bound["ok"] is True
    governance = adapter.invoke(
        "governance.plan",
        {"mode": "inspect", "project_id": "project-a", "plan_id": "plan_alpha"},
    )
    assert governance["ok"] is False
    assert governance["error"]["code"] == "AUTHORITY_DENIED"
    switch = adapter.invoke(
        "task_main.context",
        {"mode": "switch", "project_selector": "project-a"},
    )
    assert switch["ok"] is False
    assert switch["error"]["code"] == "CONTEXT_SWITCH_UNAVAILABLE"


def test_project_only_switch_eligibility_is_plan_required(tmp_path: Path) -> None:
    from test_m2_w2_context_switch_transition import _roots, _state
    from aota_forge.core.task_main_context import TrustedProjectPlanIdentity
    from aota_forge.runtime.task_main.context_switch_eligibility import (
        REASON_STALE_PLAN_AUTHORITY,
        derive_switch_eligibility,
    )

    identity = TrustedProjectPlanIdentity(project_id="transition-project")
    eligibility = derive_switch_eligibility(
        identity,
        _roots(tmp_path),
        _state(tmp_path),
        (),
    )
    assert eligibility.eligible is False
    assert REASON_STALE_PLAN_AUTHORITY in eligibility.reason_codes


def test_readback_mismatch_rolls_back_to_unbound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(tmp_path)
    adapter = _adapter(world)
    original = _SharedAotaMcpAdapter.invoke

    def mismatching_invoke(self, operation, arguments):
        result = original(self, operation, arguments)
        if operation == "result.hydrate" and result.get("ok"):
            payload = result.get("payload")
            if isinstance(payload, dict) and isinstance(payload.get("content"), str):
                decoded = json.loads(payload["content"])
                decoded["origin_session_ref"] = "foreign-session"
                payload["content"] = json.dumps(decoded, sort_keys=True, separators=(",", ":"))
        return result

    monkeypatch.setattr(_SharedAotaMcpAdapter, "invoke", mismatching_invoke)
    result = adapter.invoke(
        "task_main.context",
        {"mode": "bind", "project_selector": "project-a"},
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "READBACK_MISMATCH"
    assert adapter._bound_delegate is None
    assert _inspect(adapter)["payload"]["state"] == "unbound"


def test_mcp_surface_remains_one_public_tool() -> None:
    assert MCP_PUBLIC_TOOL_COUNT == 1
