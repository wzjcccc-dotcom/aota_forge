"""AF #59 M1 — thin native host & ref-scoped authority focused tests.

Deterministic, network-free coverage for the M1 acceptance criteria:

* global AOTA MCP starts in an ordinary workspace (no active_binding.json,
  no preparation, no special instance directory)
* role.bootstrap is optional (no mechanical first-action gate)
* first-message Plan grammar / one-chat-one-Plan ceremony removed from the
  production source
* native session directory semantics preserved (no AF interception seam)
* operation-time ref-scoped authority allow/deny (reads usable, unapproved
  side effects denied, approved bounded refs permitted, wrong refs denied)
* approval is a current server-side Plan fact (never session state)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = REPO_ROOT / "scripts" / "opencode_aota_mcp_server.py"

PROJECT_ID = "af59test"
SOURCE_REPOSITORY = "wzjcccc-dotcom/af59test"
PLAN_REF = f"{SOURCE_REPOSITORY}#59"

PLAN_BODY_TEMPLATE = """# [PLAN] AF #59 deterministic fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
PROJECT_ID={project_id}
SOURCE_REPOSITORY={source_repository}
KNOWN_SOURCE_ROOT={root}
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED={approval}
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```

## M1

#### M1/W1 — Fixture work item one
Deterministic fixture work item one.
Acceptance: W1=PASS

#### M1/W2 — Fixture work item two
Deterministic fixture work item two.
Acceptance: W2=PASS
"""


class _PlanSnapshot:
    def __init__(self, body: str) -> None:
        self.body = body
        self.revision = "2026-09-16T00:00:00Z"
        self.digest = "f" * 64


def _fake_plan_loader(body: str):
    def _loader(plan_ref: str):
        assert plan_ref == PLAN_REF
        return _PlanSnapshot(body)

    return _loader


FIXTURE_MANIFEST = """schema_version: 1
project:
  id: af59test
  name: AF59 Test
  kind: forge-core
  status: active
summary: AF 59 deterministic fixture project.
capabilities:
  - forge-core
paths:
  source_root: .
  source:
    - src/
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
  requires_human_checkpoint: true
codegraph:
  enabled: false
  index_location: .codegraph/
plan:
  active_plan_id: null
constraints: []
"""


@pytest.fixture()
def fixture_project(tmp_path: Path):
    """A deterministic trusted project + registry + fake live Plan loader."""
    root = tmp_path / "project"
    root.mkdir()
    (root / ".aota").mkdir()
    (root / ".aota" / "project.yaml").write_text(FIXTURE_MANIFEST, encoding="utf-8")
    (root / "README.md").write_text("# af59test fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", f"https://github.com/{SOURCE_REPOSITORY}.git"],
        check=True,
    )
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"ws-af59": {"candidates": [str(root)]}}), encoding="utf-8")
    return root, registry


def _adapter(root: Path, registry: Path, approval: str):
    from aota_forge.mcp_transport import UnboundHostContext, _UnboundAotaAdapter

    body = PLAN_BODY_TEMPLATE.format(
        project_id=PROJECT_ID,
        source_repository=SOURCE_REPOSITORY,
        root=str(root),
        approval=approval,
    )
    context = UnboundHostContext(
        repo_root=str(REPO_ROOT),
        registry_path=str(registry),
        runtime_config_path=os.environ["AOTA_FORGE_RUNTIME_CONFIG"],
    )
    adapter = _UnboundAotaAdapter(context)
    resolver = adapter._authority_resolver()
    resolver._plan_loader = _fake_plan_loader(body)
    return adapter


# ---------------------------------------------------------------------------
# W1 — ceremony removal at the source level
# ---------------------------------------------------------------------------


def test_no_interactive_ingress_package_in_production():
    assert not (REPO_ROOT / "aota_forge" / "interactive_ingress").exists()
    assert not (REPO_ROOT / "deploy" / "openchamber-af-interactive").exists()
    assert not (REPO_ROOT / "scripts" / "af58_m2_interactive_probe.py").exists()


def test_no_ceremony_tokens_in_production_source():
    forbidden = (
        "af_interactive_preparation",
        "af_interactive_schema",
        "AF_INTERACTIVE_WORKSPACE_ROOT",
        "AF_INTERACTIVE_INGRESS_ENABLED",
        "extract_canonical_plan_refs",
        "require_single_plan_ref",
        "SESSION_ALREADY_BOUND_DIFFERENT_PLAN",
        "INTERACTIVE_APPROVAL_GATED_OPERATIONS",
        "INTERACTIVE_APPROVAL_GATE_ENFORCED",
        "trusted_plan_state",
    )
    for path in (REPO_ROOT / "aota_forge").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{token!r} still present in {path}"


def test_mcp_wrapper_supports_ordinary_workspace_without_pointer():
    source = WRAPPER.read_text(encoding="utf-8")
    assert "AOTA_GLOBAL_MCP" in source
    assert "_run_unbound" in source
    assert "active_binding.json" in source  # pointer remains the bound-mode channel


def test_approval_is_server_side_fact_not_session_state():
    from aota_forge import mcp_transport as mt

    assert mt.APPROVAL_IS_CURRENT_SERVER_SIDE_FACT is True
    assert mt.APPROVAL_IS_SESSION_STATE is False
    assert mt.MCP_AVAILABILITY_IS_NOT_AUTHORITY is True
    assert mt.SESSION_BINDING_REQUIRED_FOR_MCP is False


def test_ref_scoped_authority_module_invariants():
    from aota_forge.composition import ref_scoped_authority as rsa

    assert rsa.SESSION_BINDING_REQUIRED is False
    assert rsa.REF_IS_AUTHORITY_LOCATOR is True
    assert rsa.REF_IS_AUTHORITY_SOURCE is False
    assert rsa.NEW_AUTHORITY_ENGINE_CREATED is False
    assert rsa.SECOND_SESSION_SYSTEM_CREATED is False
    assert rsa.SECOND_MCP_SERVER_CREATED is False


# ---------------------------------------------------------------------------
# W1/W3 — global MCP starts without binding (real stdio, ordinary directory)
# ---------------------------------------------------------------------------


def test_unbound_mcp_stdio_lists_single_tool_and_bootstrap(tmp_path: Path):
    mcp = pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    ordinary = tmp_path / "ordinary-workspace"
    ordinary.mkdir()
    assert not (ordinary / ".aota" / "opencode" / "active_binding.json").exists()

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(WRAPPER)],
        cwd=str(ordinary),
        env={
            **os.environ,
            "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )

    async def _run() -> None:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert [tool.name for tool in listed.tools] == ["aota.invoke"]
                boot = await session.call_tool("aota.invoke", {"operation": "role.bootstrap", "arguments": {}})
                payload = boot.structured_content if hasattr(boot, "structured_content") else boot.structuredContent
                assert payload["ok"] is True
                assert payload["payload"]["HOST_SESSION"] == "unbound"
                assert payload["payload"]["SESSION_BINDING_REQUIRED"] is False

    asyncio.run(_run())


def test_unbound_operations_without_ref_fail_closed(fixture_project):
    root, registry = fixture_project
    adapter = _adapter(root, registry, "no")
    result = adapter.invoke("github.issue.read", {})
    assert result["ok"] is False
    assert result["error"]["code"] == "PLAN_REF_REQUIRED"
    # AF #59 M1 acceptance repair R4: the ref-scoped Git reads also require the
    # canonical plan_ref locator; without it they fail closed (never a bare or
    # session-derived repo target).
    git_without_ref = adapter.invoke("git.status", {})
    assert git_without_ref["ok"] is False
    assert git_without_ref["error"]["code"] == "PLAN_REF_REQUIRED"
    denied = adapter.invoke(
        "workspace.write", {"path": "x.txt", "content": "x", "mode": "create"}
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] == "AUTHORITY_DENIED"


# ---------------------------------------------------------------------------
# W2 — operation-time ref-scoped authority allow/deny
# ---------------------------------------------------------------------------


def test_unapproved_plan_read_allowed_side_effect_denied(fixture_project):
    root, registry = fixture_project
    adapter = _adapter(root, registry, "no")

    read = adapter.invoke(
        "workspace.read",
        {"plan_ref": PLAN_REF, "path": "README.md", "max_bytes": 200},
    )
    assert read["ok"] is True

    update = adapter.invoke(
        "github.issue.update",
        {"plan_ref": PLAN_REF, "state": "closed", "expected_updated_at": "2026-09-16T00:00:00Z"},
    )
    assert update["ok"] is False
    assert update["error"]["code"] == "MILESTONE_APPROVAL_REQUIRED"

    handoff = adapter.invoke(
        "handoff.write",
        {
            "mode": "work_item",
            "plan_ref": PLAN_REF,
            "payload": {"work_role": "coder", "objective": "x", "bounded_scope": "y"},
        },
    )
    assert handoff["ok"] is False
    assert handoff["error"]["code"] == "MILESTONE_APPROVAL_REQUIRED"

    start = adapter.invoke(
        "task.start",
        {"plan_ref": PLAN_REF, "role": "coder", "handoff_ref": "handoff:work_item:" + "0" * 64},
    )
    assert start["ok"] is False
    assert start["error"]["code"] == "MILESTONE_APPROVAL_REQUIRED"


def test_approved_bounded_refs_permitted_operation_succeeds(fixture_project):
    root, registry = fixture_project
    adapter = _adapter(root, registry, "yes")

    read = adapter.invoke(
        "workspace.read",
        {"plan_ref": PLAN_REF, "path": "README.md", "max_bytes": 200},
    )
    assert read["ok"] is True

    handoff = adapter.invoke(
        "handoff.write",
        {
            "mode": "work_item",
            "plan_ref": PLAN_REF,
            "payload": {"work_role": "coder", "objective": "fixture", "bounded_scope": "fixture scope"},
        },
    )
    assert handoff["ok"] is True, handoff.get("error")
    ref = (handoff.get("payload") or {}).get("ref") or (handoff.get("payload") or {}).get("handoff_ref")
    assert isinstance(ref, str) and ref.startswith("handoff:")
    assert (root / ".aota" / "handoffs").is_dir()


def test_wrong_task_ref_denied_at_operation_boundary(fixture_project):
    root, registry = fixture_project
    adapter = _adapter(root, registry, "yes")

    missing = adapter.invoke(
        "task.start",
        {"plan_ref": PLAN_REF, "role": "coder", "handoff_ref": "handoff:work_item:" + "a" * 64},
    )
    assert missing["ok"] is False
    assert missing["error"]["code"] in ("UNKNOWN_REF", "DIGEST_MISMATCH", "GOVERNED_OPERATION_FAILURE")

    wrong_role = adapter.invoke(
        "task.start",
        {"plan_ref": PLAN_REF, "role": "reviewer", "handoff_ref": "handoff:work_item:" + "a" * 64},
    )
    assert wrong_role["ok"] is False


def test_wrong_project_ref_denied(fixture_project):
    root, registry = fixture_project
    adapter = _adapter(root, registry, "yes")
    denied = adapter.invoke(
        "workspace.read",
        {"plan_ref": "wzjcccc-dotcom/unknown-project#1", "path": "README.md"},
    )
    assert denied["ok"] is False
    # The fake loader only knows PLAN_REF; the mismatch fails closed.
    assert denied["error"]["code"] in ("PLAN_UNREADABLE", "PROJECT_RESOLUTION_FAILED", "PROJECT_ROOT_MISMATCH")


def test_override_plan_ref_rejected_by_fake_loader(fixture_project, monkeypatch):
    root, registry = fixture_project
    adapter = _adapter(root, registry, "yes")

    other = types.SimpleNamespace(body="", revision=None, digest=None)
    monkeypatch.setattr(adapter._authority_resolver(), "_plan_loader", lambda ref: other)
    denied = adapter.invoke("workspace.read", {"plan_ref": PLAN_REF, "path": "README.md"})
    assert denied["ok"] is False
    assert denied["error"]["code"] == "PLAN_UNREADABLE"


# ---------------------------------------------------------------------------
# Native session semantics preserved
# ---------------------------------------------------------------------------


def test_worker_instance_directory_remains_worktree_scoped():
    from aota_forge.adapters.opencode.task_main import (
        instance_directory,
        worker_instance_key,
    )

    key = worker_instance_key("af59-test-task")
    assert key.startswith("worker-")
    path = instance_directory(REPO_ROOT, key)
    assert str(path).startswith(str(REPO_ROOT / ".aota" / "opencode" / "instances"))


def test_wrapper_bound_mode_still_requires_valid_pointer_material(tmp_path: Path):
    """A stale/invalid pointer must fail closed even though unbound mode exists."""
    instance = tmp_path / "instance"
    (instance / ".aota" / "opencode").mkdir(parents=True)
    (instance / ".aota" / "opencode" / "active_binding.json").write_text(
        json.dumps({"schema_version": "1", "kind": "not-a-kind"}), encoding="utf-8"
    )
    proc = subprocess.run(
        [sys.executable, str(WRAPPER)],
        cwd=str(instance),
        env={**os.environ, "AOTA_FORGE_REPO_ROOT": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "pointer kind invalid" in proc.stderr
