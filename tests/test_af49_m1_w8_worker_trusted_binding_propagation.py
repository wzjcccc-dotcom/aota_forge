"""AF #49 M1/W8 — Worker Trusted Binding Propagation (I49-B006).

The accepted normal production path is::

    LLM -> handoff.write(mode=work_item) -> Control Plane source grounding
        -> durable canonical grounded work_item handoff -> task.start
        -> trusted Worker binding -> physical Worker dispatch

This suite proves:

* V1 normal path: the canonical durable grounded work_item handoff alone
  (no coordinator ``work_projection``, no operator ``work_semantics``)
  produces the trusted Worker child environment;
* exact binding identity: project / worktree / milestone / Work Item /
  canonical task id / Plan identity / trusted handoff identity;
* the negative matrix fails closed with zero physical Worker dispatch;
* the historical ``resolver returns None -> launch`` defect is gone:
  a governed dispatch whose resolver yields no binding is a typed dispatch
  rejection, not a bindingless launch;
* non-AF/generic host-client usage without the governed contract is
  unaffected;
* targeted fresh-process V2: a new process rebuilds the trusted binding from
  durable state only, and the derived environment starts the real AF MCP
  child subprocess exposing ``aota.invoke``.

Proof boundary (honest):
  PROVES=deterministic component integration through the production task-main
         bootstrap + host client + durable handoff store; no bindingless
         dispatch; fresh-process recovery; real AF MCP child import/exposure.
  DOES_NOT_PROVE=real Hermes Worker model execution, B007/B005 repairs,
         M1_V3_RERUN_3, or production acceptance.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aota_forge.adapters.hermes.host_client import (
    HermesHostClient,
    HermesHostClientError,
)
from aota_forge.composition import worker_vertical_slice as wvs
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_RELPATH,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.mcp_transport import _SharedAotaMcpAdapter
from aota_forge.runtime.task_main.coordinator import compute_work_source_digest
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    load_binding_from_envelope,
    verify_envelope,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.handoff_store import handoff_write
from aota_forge.work_plane.task_facade import TRUSTED_WORK_HANDOFF_CONTEXT_KEY
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "proj_af49w8"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#49"
PLAN_DIGEST = "d" * 64
PLAN_DIGEST_STALE = "e" * 64
ENTRY_BASE = "a" * 40
WORKTREE_ID = "wt-af49w8"
REAL_SESSION = "20260912_180000_w8real"

MARKER_W1 = "AF49W8_W1_SOURCE_ALPHA"
MARKER_W2 = "AF49W8_W2_SOURCE_BETA"
MARKER_W3 = "AF49W8_W3_SOURCE_GAMMA"

OBJECTIVE = "Implement the bounded behavior described by the authoritative source slice"
SCOPE = "bounded source-grounded implementation scope"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _plan_body() -> str:
    return f"""# [PLAN] AF49 W8 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2 -> W3
M1_WORK_ITEMS=W1, W2, W3
```

## M1

#### M1/W1 — Authoritative alpha slice
Implement {MARKER_W1} bounded behavior exactly as the Plan describes.

#### M1/W2 — Authoritative beta slice
Implement {MARKER_W2} bounded behavior exactly as the Plan describes.

#### M1/W3 — Authoritative gamma slice
Implement {MARKER_W3} bounded behavior exactly as the Plan describes.
"""


def _live(*, plan_authority: str = PLAN_AUTH, plan_digest: str = PLAN_DIGEST):
    doc = normalize_portable_plan(_plan_body(), source_revision="rev-af49w8")
    live, next_view = project_milestone_views(
        doc,
        plan_authority=plan_authority,
        plan_digest=plan_digest,
        plan_source_revision="rev-af49w8",
    )
    return live, next_view


def _project_manifest() -> str:
    return (
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
        "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n"
    )


def _runtime_config_json() -> dict:
    return {
        "executor": "hermes",
        "executable": "/bin/false",
        "concurrency": 2,
        "provider": "opencode-go",
        "model": "m",
        "bindings": {
            "task-main": {"profile": "aota-task-main"},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
        },
    }


def _semantic(work_item_ref: str = "W1") -> dict:
    return {
        "work_role": "coder",
        "task_kind": "af49w8-normal-path",
        "objective": OBJECTIVE,
        "bounded_scope": SCOPE,
        "validation_expectations": ["focused validation"],
        "semantic_stop_expectations": ["stop if trusted binding inconsistent"],
        "work_item_ref": {"ref": work_item_ref},
    }


def _sandbox(
    root: Path,
    *,
    worktree_id: str = WORKTREE_ID,
    project_id: str = PROJECT_ID,
) -> WorktreeSandboxBoundary:
    return WorktreeSandboxBoundary(
        workspace_id="ws-af49w8",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        worktree_id=worktree_id,
        worktree_root=str(root),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _write_grounded(
    root: Path,
    live,
    *,
    wid: str = "W1",
    source_wid: str | None = None,
    milestone_id: str | None = None,
    plan_ref: str | None = None,
    plan_digest: str | None = None,
    source_digest: str | None = None,
    sandbox: WorktreeSandboxBoundary | None = None,
    semantic: dict | None = None,
):
    sb = sandbox or _sandbox(root)
    source = live.get_work_source_slice(source_wid or wid)
    assert source is not None
    provenance = {
        "plan_digest": plan_digest if plan_digest is not None else live.plan_digest,
        "work_source_digest": (
            source_digest
            if source_digest is not None
            else compute_work_source_digest(source.source_text)
        ),
        "grounding": "task_main_authoritative_work_source",
    }
    return handoff_write(
        mode="work_item",
        semantic=semantic if semantic is not None else _semantic(wid),
        caller_role="task-main",
        sandbox=sb,
        plan_ref=plan_ref if plan_ref is not None else live.plan_authority,
        milestone_id=milestone_id if milestone_id is not None else live.milestone_id,
        work_item_id=wid,
        provenance=provenance,
    )


def _handoff_dir(root: Path) -> Path:
    return root / ".aota" / "handoffs"


def _copy_handoff(source_digest: str, source_root: Path, target_root: Path) -> str:
    target = _handoff_dir(target_root)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{source_digest}.json").write_text(
        (_handoff_dir(source_root) / f"{source_digest}.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return source_digest


class _SpawnRecorder:
    """Records physical supervisor spawns (Popen) without launching anything."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(pid=424242, poll=lambda: None)


def _host_from_dispatcher(dispatcher) -> Any:
    for adapter in dispatcher.registry._adapters.values():
        host = getattr(adapter, "_host_client", None)
        if host is not None:
            return host
    raise AssertionError("no Hermes host client on the dispatcher graph")


def _production_env(
    tmp_path: Path,
    live,
    *,
    run_id: str = "main",
    origin: str = REAL_SESSION,
    worktree_id: str = WORKTREE_ID,
):
    run = f"{run_id}-{uuid.uuid4().hex[:6]}"
    root = tmp_path / f"wt_{run}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(_project_manifest(), encoding="utf-8")
    cfg = tmp_path / f"runtime_{run}.json"
    cfg.write_text(json.dumps(_runtime_config_json()), encoding="utf-8")
    coord = root / ".aota" / "coordinator.json"
    exec_path = root / ".aota" / "execution.json"
    coord.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id=worktree_id,
        coordinator_store_path=coord,
        execution_store_path=exec_path,
        runtime_config_path=cfg,
        origin_task_main_session_ref=origin,
        live_plan_view=live,
        next_milestone_view=None,
        work_semantics=None,
    )
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    try:
        binding = try_build_task_main_binding()
    finally:
        os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
    assert binding is not None
    dispatcher = binding.trusted_task_main_context.control_service._dispatcher
    host = _host_from_dispatcher(dispatcher)
    recorder = _SpawnRecorder()
    host._popen_factory = recorder
    host.runtime_root = tmp_path / f"hermes-runs-{run}"
    return SimpleNamespace(
        root=root,
        cfg=cfg,
        coord_path=coord,
        exec_path=exec_path,
        binding=binding,
        dispatcher=dispatcher,
        host=host,
        spawn=recorder,
        live=live,
        sandbox=_sandbox(root, worktree_id=worktree_id),
        worktree_id=worktree_id,
    )


def _dispatch_payload(
    *,
    ref: str | None = None,
    digest: str | None = None,
    wi: str = "W1",
    canonical_task_id: str = "proj_af49w8:M1:W1:attempt-1",
    declared: bool = True,
    mode: str = "work_item",
) -> dict:
    working: dict[str, Any] = {
        "bounded_scope": SCOPE,
        "handoff_digest": "0" * 64,
        "task_kind": "af49w8-normal-path",
        "work_role": "coder",
        "refs": {"work_item_ref": {"ref": wi}},
    }
    if declared:
        working[TRUSTED_WORK_HANDOFF_CONTEXT_KEY] = {
            "ref": ref,
            "digest": digest,
            "mode": mode,
        }
    return {
        "profile": "aota-worker",
        "instruction": "bounded work",
        "context": {
            "canonical_task_id": canonical_task_id,
            "working_context": working,
        },
        "artifacts": [],
        "constraints": {},
        "capability_requirements": {},
        "result_expectations": {},
        "operation": "task_dispatch",
        "package_id": f"pkg-{uuid.uuid4().hex[:8]}",
    }


@pytest.fixture(autouse=True)
def _isolated_env():
    saved = dict(os.environ)
    reset_execution_dispatcher()
    for key in list(os.environ):
        if key.startswith("AOTA_"):
            os.environ.pop(key, None)
    yield
    os.environ.clear()
    os.environ.update(saved)
    reset_execution_dispatcher()


def _expect_governed_rejection(env, payload: dict, *, inner: str | None = None) -> None:
    with pytest.raises(TrustedBindingError) as resolver_exc:
        env.host._worker_env_resolver(payload)
    assert getattr(resolver_exc.value, "code", None) == "WORKER_BINDING_UNAVAILABLE"
    if inner is not None:
        assert inner in str(resolver_exc.value), str(resolver_exc.value)
    with pytest.raises(HermesHostClientError) as dispatch_exc:
        env.host.dispatch(payload)
    assert dispatch_exc.value.code == "WORKER_BINDING_UNAVAILABLE"
    assert env.spawn.calls == []


# ---------------------------------------------------------------------------
# V1 — normal path, exact binding, no legacy authority
# ---------------------------------------------------------------------------


class TestNormalPathTrustedBinding:
    def test_v1_canonical_handoff_only_produces_worker_binding(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        # Fixture invariant: no legacy authority exists.
        bootstrap = json.loads((env.root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))
        assert "work_semantics" not in bootstrap
        state = FileBackedTaskMainCoordinatorStore(env.coord_path).get(f"{PROJECT_ID}:M1")
        assert state is None or not dict(getattr(state, "work_projections", {}) or {})

        ref = _write_grounded(env.root, live)
        adapter = _SharedAotaMcpAdapter(env.binding)
        resp = adapter.invoke("task.start", {"role": "coder", "handoff_ref": ref.ref})
        assert resp["ok"] is True, resp.get("error")
        assert len(env.spawn.calls) == 1

        child = env.spawn.calls[0]["env"]
        assert PRE_RESOLVED_BINDING_ENV in child
        locator = child[PRE_RESOLVED_BINDING_ENV]
        assert Path(locator).is_file()
        kind, envelope = verify_envelope(locator)
        assert kind == "worker"
        assert envelope["project_id"] == PROJECT_ID
        assert envelope["worktree_id"] == WORKTREE_ID
        assert envelope["worktree_root"] == str(env.root.resolve())
        assert envelope["canonical_task_id"] == resp["payload"]["task_id"]
        # Repo-root/import binding is produced by the production resolver.
        assert child["AOTA_FORGE_REPO_ROOT"] == str(_repo_root())
        assert child["PYTHONPATH"].split(os.pathsep)[0] == str(_repo_root())

    def test_v1_derived_binding_matches_exact_trusted_source(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        adapter = _SharedAotaMcpAdapter(env.binding)
        resp = adapter.invoke("task.start", {"role": "coder", "handoff_ref": ref.ref})
        assert resp["ok"] is True, resp.get("error")
        locator = env.spawn.calls[0]["env"][PRE_RESOLVED_BINDING_ENV]
        worker = load_binding_from_envelope(locator)
        assert worker.project_id == PROJECT_ID
        assert worker.worktree_id == WORKTREE_ID
        assert worker.sandbox.project_id == PROJECT_ID
        assert worker.sandbox.worktree_id == WORKTREE_ID
        assert worker.handoff.work_item_ref is not None
        assert worker.handoff.work_item_ref.ref == "W1"
        assert worker.handoff.milestone_ref is not None
        assert worker.handoff.milestone_ref.ref == "M1"
        assert worker.handoff.project_ref is not None
        assert worker.handoff.project_ref.ref == PROJECT_ID
        assert worker.handoff.plan_ref is not None
        assert worker.handoff.plan_ref.ref == live.plan_authority
        assert worker.handoff.plan_ref.digest == live.plan_digest
        assert worker.handoff.objective == OBJECTIVE
        assert worker.handoff.bounded_scope == SCOPE

    def test_v1_legacy_coordinator_projection_not_required(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        # No coordinator projection, no bootstrap work_semantics, no
        # submit_work_projection call anywhere on this path.
        state = FileBackedTaskMainCoordinatorStore(env.coord_path).get(f"{PROJECT_ID}:M1")
        assert state is None or not dict(getattr(state, "work_projections", {}) or {})
        adapter = _SharedAotaMcpAdapter(env.binding)
        resp = adapter.invoke("task.start", {"role": "coder", "handoff_ref": ref.ref})
        assert resp["ok"] is True, resp.get("error")
        assert len(env.spawn.calls) == 1
        locator = env.spawn.calls[0]["env"][PRE_RESOLVED_BINDING_ENV]
        kind, envelope = verify_envelope(locator)
        assert kind == "worker"
        # Trusted envelopes the durable handoff semantic identity.
        handoff = TaskHandoff.from_dict(envelope["handoff"])
        assert handoff.work_item_ref.ref == "W1"
        assert handoff.plan_ref.digest == live.plan_digest


# ---------------------------------------------------------------------------
# Negative matrix — typed fail-closed, zero physical Worker dispatch
# ---------------------------------------------------------------------------


class TestNegativeMatrixFailClosed:
    def test_missing_canonical_handoff(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        missing_ref = f"handoff://{PROJECT_ID}/{WORKTREE_ID}/work_item/deadbeef/{'0' * 64}"
        payload = _dispatch_payload(ref=missing_ref, digest="0" * 64)
        _expect_governed_rejection(env, payload, inner="not found")

    def test_handoff_mode_not_work_item(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        result_ref = handoff_write(
            mode="result",
            semantic={"summary": "a result, not a work item"},
            caller_role="coder",
            sandbox=env.sandbox,
            task_id="task-w8-result",
        )
        payload = _dispatch_payload(ref=result_ref.ref, digest=result_ref.digest)
        _expect_governed_rejection(env, payload, inner="not work_item")

    def test_foreign_project_handoff(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        foreign_root = tmp_path / "foreign-project-root"
        foreign_root.mkdir(parents=True, exist_ok=True)
        foreign = _write_grounded(
            foreign_root,
            live,
            sandbox=_sandbox(foreign_root, project_id="foreign-project"),
        )
        _copy_handoff(foreign.digest, foreign_root, env.root)
        payload = _dispatch_payload(ref=foreign.ref, digest=foreign.digest)
        _expect_governed_rejection(env, payload, inner="cross-project")

    def test_foreign_worktree_binding(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        foreign_root = tmp_path / "foreign-worktree-root"
        foreign_root.mkdir(parents=True, exist_ok=True)
        foreign = _write_grounded(
            foreign_root,
            live,
            sandbox=_sandbox(foreign_root, worktree_id="foreign-wt"),
        )
        _copy_handoff(foreign.digest, foreign_root, env.root)
        payload = _dispatch_payload(ref=foreign.ref, digest=foreign.digest)
        _expect_governed_rejection(env, payload, inner="cross-worktree")

    def test_wrong_milestone(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        foreign = _write_grounded(env.root, live, milestone_id="M2")
        payload = _dispatch_payload(ref=foreign.ref, digest=foreign.digest)
        _expect_governed_rejection(env, payload, inner="MILESTONE_MISMATCH")

    def test_wrong_work_item(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        w2 = _write_grounded(env.root, live, wid="W2")
        payload = _dispatch_payload(ref=w2.ref, digest=w2.digest, wi="W1")
        _expect_governed_rejection(env, payload, inner="WORK_ITEM_MISMATCH")

    def test_ungoverned_work_item(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        rogue = _write_grounded(env.root, live, wid="W9", source_wid="W1")
        payload = _dispatch_payload(ref=rogue.ref, digest=rogue.digest, wi="W9")
        _expect_governed_rejection(env, payload, inner="WORK_ITEM_MISMATCH")

    def test_plan_digest_mismatch(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        stale = _write_grounded(env.root, live, plan_digest=PLAN_DIGEST_STALE)
        payload = _dispatch_payload(ref=stale.ref, digest=stale.digest)
        _expect_governed_rejection(env, payload, inner="PLAN_DIGEST_MISMATCH")

    def test_stale_handoff_after_plan_revision_advance(self, tmp_path: Path) -> None:
        current_live, _ = _live()
        stale_live, _ = _live(plan_digest=PLAN_DIGEST_STALE)
        env = _production_env(tmp_path, stale_live)
        grounded_to_current = _write_grounded(env.root, current_live)
        payload = _dispatch_payload(ref=grounded_to_current.ref, digest=grounded_to_current.digest)
        _expect_governed_rejection(env, payload, inner="PLAN_DIGEST_MISMATCH")

    def test_work_source_digest_mismatch(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        wrong_source = "AF49W8_TAMPERED_SOURCE content"
        bad = _write_grounded(
            env.root,
            live,
            source_digest=compute_work_source_digest(wrong_source),
        )
        payload = _dispatch_payload(ref=bad.ref, digest=bad.digest)
        _expect_governed_rejection(env, payload, inner="WORK_SOURCE_DIGEST_MISMATCH")

    def test_tampered_handoff(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        artifact = _handoff_dir(env.root) / f"{ref.digest}.json"
        data = json.loads(artifact.read_text(encoding="utf-8"))
        data["semantic"]["objective"] = "tampered by an attacker"
        artifact.write_text(json.dumps(data), encoding="utf-8")
        payload = _dispatch_payload(ref=ref.ref, digest=ref.digest)
        _expect_governed_rejection(env, payload, inner="tamper")

    def test_ambiguous_declared_digest(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        # Declared dispatch digest contradicts the exact durable handoff identity.
        payload = _dispatch_payload(ref=ref.ref, digest="0" * 64)
        _expect_governed_rejection(env, payload, inner="digest")

    def test_malformed_governed_record(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        payload = _dispatch_payload(ref=ref.ref, digest=ref.digest)
        payload["context"]["working_context"][TRUSTED_WORK_HANDOFF_CONTEXT_KEY] = "not-a-mapping"
        _expect_governed_rejection(env, payload, inner="mapping")

    def test_resolver_internal_failure(self, tmp_path: Path, monkeypatch) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        payload = _dispatch_payload(ref=ref.ref, digest=ref.digest)

        import aota_forge.work_plane.handoff_store as handoff_store_mod

        def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("injected handoff store failure")

        monkeypatch.setattr(handoff_store_mod, "handoff_open", _boom)
        _expect_governed_rejection(env, payload, inner="RuntimeError")

    def test_binding_builder_failure(self, tmp_path: Path, monkeypatch) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        payload = _dispatch_payload(ref=ref.ref, digest=ref.digest)

        def _boom(*args: Any, **kwargs: Any):
            raise RuntimeError("injected binding builder failure")

        monkeypatch.setattr(wvs, "build_worker_child_environment", _boom)
        _expect_governed_rejection(env, payload, inner="binding builder failure")


# ---------------------------------------------------------------------------
# Historical None regression + non-AF compatibility
# ---------------------------------------------------------------------------


class TestBindinglessDispatchForbidden:
    def test_resolver_none_never_launches_governed_worker(self, tmp_path: Path) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live)
        ref = _write_grounded(env.root, live)
        payload = _dispatch_payload(ref=ref.ref, digest=ref.digest)
        # Exact historical defect shape: the resolver yields no binding.
        env.host._worker_env_resolver = lambda _payload: None
        with pytest.raises(HermesHostClientError) as excinfo:
            env.host.dispatch(payload)
        assert excinfo.value.code == "DISPATCH_REJECTED"
        assert env.spawn.calls == []

    def test_generic_host_client_without_governed_contract_unaffected(self, tmp_path: Path) -> None:
        calls: list[dict] = []

        class FakePopen:
            def __init__(self, *args, **kwargs):
                calls.append(kwargs)
                self.pid = 515151

            def poll(self):
                return None

        runs = tmp_path / "generic-runs"
        runs.mkdir()
        client = HermesHostClient(
            "/bin/false",
            default_cwd=str(tmp_path),
            validate_launcher=False,
            runtime_root=runs,
            popen_factory=FakePopen,
            worker_env_resolver=lambda _payload: None,
        )
        payload = _dispatch_payload(declared=False)
        result = client.dispatch(payload)
        assert result["status"] == "pending"
        assert len(calls) == 1
        env = calls[0]["env"]
        assert PRE_RESOLVED_BINDING_ENV not in env


# ---------------------------------------------------------------------------
# Targeted fresh-process V2 + real AF MCP child subprocess
# ---------------------------------------------------------------------------


async def _mcp_child_tool_names(child_env: dict[str, str], *, cwd: str) -> list[str]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "aota_forge.composition.worker_vertical_slice", "--mcp-server"],
        env=child_env,
        cwd=cwd,
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            return [tool.name for tool in listed.tools]


class TestFreshProcessRecovery:
    def test_v2_fresh_process_recovers_binding_and_starts_real_af_mcp_child(
        self, tmp_path: Path
    ) -> None:
        live, _ = _live()
        env = _production_env(tmp_path, live, run_id="v2")
        ref = _write_grounded(env.root, live)
        durable = json.loads((_handoff_dir(env.root) / f"{ref.digest}.json").read_text(encoding="utf-8"))
        payload = _dispatch_payload(
            ref=ref.ref,
            digest=ref.digest,
            canonical_task_id=f"{PROJECT_ID}:M1:W1:attempt-1",
        )
        probe_out = tmp_path / "fresh-resolver-env.json"
        probe_script = Path(__file__).with_name("af49_w8_fresh_resolver_probe.py")
        probe_env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("AOTA_")},
            "AOTA_W3_MCP_ROOT": str(env.root),
            "AOTA_W8_PROBE_OUTPUT": str(probe_out),
            "AOTA_W8_PROBE_PAYLOAD": json.dumps(payload),
            "PYTHONPATH": str(_repo_root())
            + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
        }
        proc = subprocess.run(
            [sys.executable, str(probe_script)],
            env=probe_env,
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert probe_out.is_file()
        probe = json.loads(probe_out.read_text(encoding="utf-8"))
        assert probe["ok"] is True, probe.get("error")
        assert probe["project_id"] == PROJECT_ID
        assert probe["worktree_id"] == WORKTREE_ID

        resolved_env: dict[str, str] = probe["env"]
        assert PRE_RESOLVED_BINDING_ENV in resolved_env
        assert resolved_env["AOTA_FORGE_REPO_ROOT"] == str(_repo_root())
        assert resolved_env["PYTHONPATH"].split(os.pathsep)[0] == str(_repo_root())
        locator = resolved_env[PRE_RESOLVED_BINDING_ENV]
        assert Path(locator).is_file()
        kind, envelope = verify_envelope(locator)
        assert kind == "worker"
        assert envelope["project_id"] == PROJECT_ID
        assert envelope["worktree_id"] == WORKTREE_ID
        assert envelope["canonical_task_id"] == f"{PROJECT_ID}:M1:W1:attempt-1"
        handoff = TaskHandoff.from_dict(envelope["handoff"])
        assert handoff.work_item_ref.ref == durable["envelope"]["work_item_id"]
        assert handoff.milestone_ref.ref == durable["envelope"]["milestone_id"]
        assert handoff.plan_ref.digest == durable["envelope"]["provenance"]["plan_digest"]
        assert handoff.objective == durable["semantic"]["objective"]

        tools = asyncio.run(_mcp_child_tool_names(resolved_env, cwd=str(env.root)))
        assert tools == ["aota.invoke"]
