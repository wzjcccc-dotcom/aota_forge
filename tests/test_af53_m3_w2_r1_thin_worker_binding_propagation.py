"""AF #53 M3/W2-R1 — Thin Worker Binding Propagation (I53-B001 bounded repair).

Repairs the live defect: thin production ``task.start`` reached the real
``HermesHostClient`` and physically spawned a Worker, but thin composition
wired no trusted Worker env resolver, so the Worker child received no
``AOTA_PRE_RESOLVED_BINDING`` envelope and the production Worker MCP child
failed closed with ``MISSING_RUNTIME_CONTEXT``. The second integrity problem
(governed Worker payload + missing resolver => physical bindingless launch)
is repaired by an explicit fail-closed guard before any physical spawn.

Required proof (R1-R12):

* R1 thin composition wires a real governed Worker env resolver into the
  production dispatcher/host-client seam;
* R2 thin ``task.start`` produces the canonical pre-resolved binding envelope
  in the Worker child environment;
* R3 a spawned real AF Worker MCP probe resolves the canonical
  ``TrustedWorkerBinding`` (no ``MISSING_RUNTIME_CONTEXT``);
* R4 the Worker envelope is tied to the same grounded durable handoff used at
  ``task.start`` (project/worktree/milestone/Work/plan identity preserved);
* R5 role mismatch still fails closed before any child dispatch (F2);
* R6/R7 a governed payload without a resolver, or with a resolver that
  supplies no valid trusted child env, fails closed before physical spawn
  (zero supervisor launches, no false durable dispatch success);
* R8 foreign project/worktree handoffs fail closed;
* R9 the fresh-process thin production path loads no legacy workflow module;
* R10 the legacy Worker propagation path is preserved (existing accepted
  suites rerun alongside this one);
* R11 the Worker MCP child still exposes the single canonical ``aota.invoke``;
* R12 the trusted operator 900s Worker execution policy is preserved.

PROVES=deterministic V1/V2 repair proof over the real thin composition, real
       production dispatcher, real HermesHostClient and real AF MCP child
       binding resolution; governed dispatch without a valid resolver never
       physically spawns a Worker.
DOES_NOT_PROVE=real external LLM task-main execution, full M3/W2 Calculator
       dogfood, task.return/Card/reentry, production cutover or I53-B001
       closure.

This suite adds tests only. It introduces no new execution engine, authority
engine, Worker binding ontology, envelope protocol or MCP tool plane.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
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
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.composition.worker_vertical_slice import (
    GOVERNED_WORKER_ENV_RESOLVER_OWNER,
    WORKER_BINDING_SOURCE,
)
from aota_forge.core.execution.durable_state import ExecutionPhase
from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.mcp_transport import MCP_PUBLIC_TOOLS
from aota_forge.runtime.config import DEFAULT_WORKER_EXECUTION_TIMEOUT_SECONDS
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

REPO_ROOT = Path(__file__).resolve().parents[1]

PROJECT_ID = "aota_forge"
WORKTREE_ID = "wt-thin"
ORIGIN_SESSION = "20260913_af53_m3w2r1_real_parent"
REAL_SESSION = "20260913_af53_m3w2r1_real_parent"

PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#53"
PLAN_DIGEST = "d" * 64

LEGACY_FORBIDDEN_MODULES = (
    "aota_forge.runtime.task_main.control",
    "aota_forge.runtime.task_main.coordinator",
    "aota_forge.runtime.task_main.reconciliation",
)

PROJECT_MANIFEST = (
    "schema_version: 1\nproject:\n"
    "  id: {project_id}\n  name: t\n  kind: test\n  status: active\n"
    "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
    "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
    "plan:\n  active_plan_id: null\nconstraints: []\n"
)


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    """Composition binds the process-global canonical dispatcher seam; isolate it."""
    reset_execution_dispatcher()
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SpawnRecorder:
    """Records physical supervisor spawns (Popen) without launching anything."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(pid=424242, poll=lambda: None)


def _make_project_root(tmp_path: Path, project_id: str = PROJECT_ID) -> Path:
    root = tmp_path / f"wt-{project_id}"
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        PROJECT_MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    return root


def _write_runtime_config(
    tmp_path: Path,
    executable: Path,
    *,
    name: str = "runtime-thin.json",
    runtime_path: str | None = "thin",
    timeout_seconds: int | None = None,
) -> Path:
    doc: dict[str, Any] = {
        "executor": "hermes",
        "executable": str(executable),
        "concurrency": 1,
        "provider": "aota-test-provider",
        "model": "aota-test-model",
        "bindings": {
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
            "task-main": {"profile": "aota-task-main"},
        },
    }
    if runtime_path is not None:
        doc["runtime_path"] = runtime_path
    if timeout_seconds is not None:
        doc["worker_execution_timeout_seconds"] = timeout_seconds
    cfg = tmp_path / name
    cfg.write_text(json.dumps(doc, sort_keys=True), encoding="utf-8")
    return cfg


def _compose(
    tmp_path: Path,
    cfg: Path,
    *,
    project_id: str = PROJECT_ID,
    worktree_id: str = WORKTREE_ID,
    origin: str = ORIGIN_SESSION,
    host_client: Any | None = None,
    host_client_factory: Any | None = None,
    name: str | None = None,
):
    root = tmp_path / (name or f"wt-{project_id}")
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        PROJECT_MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    return compose_thin_task_main_host(
        worktree_root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        runtime_config_path=cfg,
        origin_task_main_session_ref=origin,
        host_client=host_client,
        host_client_factory=host_client_factory,
    )


def _real_host_client(host: Any) -> Any:
    for adapter in host.execution_dispatcher.registry._adapters.values():
        client = getattr(adapter, "_host_client", None)
        if client is not None:
            return client
    raise AssertionError("no Hermes host client on the thin dispatcher graph")


def _arm_recorder(host: Any, tmp_path: Path) -> tuple[_SpawnRecorder, Path]:
    client = _real_host_client(host)
    recorder = _SpawnRecorder()
    client._popen_factory = recorder
    runs = tmp_path / f"hermes-runs-{uuid.uuid4().hex[:8]}"
    runs.mkdir(parents=True, exist_ok=True)
    client.runtime_root = runs
    return recorder, runs


def _write_work_item(host: Any, *, role: str = "coder") -> str:
    response = host.invoke(
        "handoff.write",
        {
            "mode": "work_item",
            "payload": {
                "work_role": role,
                "task_kind": "af53-m3w2r1",
                # AF #54 M2/W2: explicit Plan/Work semantic identity (task-main owned).
                "work_item_ref": "W1",
                "milestone_ref": "M1",
                "objective": "bounded thin Worker binding propagation",
                "bounded_scope": "bounded thin scope",
                "validation_expectations": ["focused repair validation"],
                "semantic_stop_expectations": ["stop on insufficient evidence"],
            },
        },
    )
    assert response.get("is_success"), response
    return response["payload"]["ref"]


def _start(host: Any, ref: str, *, role: str = "coder") -> dict[str, Any]:
    return host.invoke("task.start", {"role": role, "handoff_ref": ref})


def _governed_payload(
    *,
    ref: str | None = None,
    digest: str | None = None,
    work_item: str = "W1",
    canonical_task_id: str = f"{PROJECT_ID}:M1:W1:abcdef12:00000000",
) -> dict[str, Any]:
    """Minimal valid host dispatch payload declaring the governed Worker contract."""
    working: dict[str, Any] = {
        "bounded_scope": "bounded thin scope",
        "handoff_digest": "0" * 64,
        "task_kind": "af53-m3w2r1",
        "work_role": "coder",
        "refs": {"work_item_ref": {"ref": work_item}},
        TRUSTED_WORK_HANDOFF_CONTEXT_KEY: {
            "ref": ref or f"handoff:work_item:{'0' * 64}",
            "digest": digest or "0" * 64,
            "mode": "work_item",
        },
    }
    return {
        "profile": "aota-worker",
        "instruction": "bounded thin work",
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


def _child_env_from_dispatch(recorder: _SpawnRecorder) -> dict[str, str]:
    assert len(recorder.calls) == 1
    env = recorder.calls[0]["env"]
    assert isinstance(env, dict)
    return env


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


def _resolve_binding_via_real_child(child_env: dict[str, str], *, cwd: str) -> dict[str, Any]:
    script = (
        "import json\n"
        "from aota_forge.composition.worker_vertical_slice import select_runtime_context\n"
        "b = select_runtime_context()\n"
        "print('AOTA_RESOLVE_JSON=' + json.dumps({"
        "'project_id': b.project_id, "
        "'worktree_id': b.worktree_id, "
        "'canonical_task_id': b.canonical_task_id, "
        "'role': b.handoff.work_role.value}))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        env=dict(child_env),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    marker = [line for line in proc.stdout.splitlines() if line.startswith("AOTA_RESOLVE_JSON=")]
    assert len(marker) == 1, proc.stdout
    assert "MISSING_RUNTIME_CONTEXT" not in proc.stderr
    return json.loads(marker[0].split("=", 1)[1])


# ---------------------------------------------------------------------------
# R1 — thin env resolver exists at the production dispatcher/host-client seam
# ---------------------------------------------------------------------------


class TestR1ThinWorkerEnvResolverPresent:
    def test_thin_composition_wires_governed_worker_env_resolver(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        client = _real_host_client(host)
        assert isinstance(client, HermesHostClient)
        resolver = client._worker_env_resolver
        assert resolver is not None
        assert callable(resolver)
        assert GOVERNED_WORKER_ENV_RESOLVER_OWNER == (
            "aota_forge/composition/worker_vertical_slice.py"
        )
        assert WORKER_BINDING_SOURCE == "trusted_server_side_runtime"
        # Generic (non-governed) host-client usage is preserved: no governed
        # record => no forced binding.
        assert resolver({"context": {"working_context": {}}}) is None

    def test_model_or_prompt_cannot_supply_the_resolver(self, tmp_path: Path) -> None:
        source = (
            REPO_ROOT / "aota_forge" / "composition" / "thin_task_main_host.py"
        ).read_text(encoding="utf-8")
        assert "create_governed_worker_env_resolver" in source
        assert "worker_env_resolver" in source


# ---------------------------------------------------------------------------
# R2 — canonical pre-resolved envelope in the Worker child environment
# ---------------------------------------------------------------------------


class TestR2EnvelopePropagated:
    def test_thin_task_start_propagates_pre_resolved_binding_envelope(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)

        ref = _write_work_item(host)
        started = _start(host, ref)
        assert started.get("is_success"), started

        child = _child_env_from_dispatch(recorder)
        assert PRE_RESOLVED_BINDING_ENV in child
        locator = child[PRE_RESOLVED_BINDING_ENV]
        assert Path(locator).is_file()
        kind, payload = verify_envelope(locator)
        assert kind == "worker"
        assert payload["project_id"] == PROJECT_ID
        assert payload["worktree_id"] == WORKTREE_ID
        assert payload["canonical_task_id"] == started["payload"]["task_id"]
        handoff = TaskHandoff.from_dict(payload["handoff"])
        assert handoff.handoff_digest == payload["handoff_digest"]
        # Canonical trusted envelope root is the real worktree.
        assert Path(payload["worktree_root"]).resolve() == host.worktree_root.resolve()


# ---------------------------------------------------------------------------
# R3 — real AF Worker MCP child resolves the canonical binding
# ---------------------------------------------------------------------------


class TestR3WorkerMCPResolves:
    def test_real_af_worker_child_resolves_trusted_binding(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)

        ref = _write_work_item(host)
        started = _start(host, ref)
        assert started.get("is_success"), started
        task_id = started["payload"]["task_id"]

        child = _child_env_from_dispatch(recorder)
        resolved = _resolve_binding_via_real_child(child, cwd=str(host.worktree_root))
        assert resolved["project_id"] == PROJECT_ID
        assert resolved["worktree_id"] == WORKTREE_ID
        assert resolved["canonical_task_id"] == task_id
        assert resolved["role"] == "coder"

        binding = load_binding_from_envelope(child[PRE_RESOLVED_BINDING_ENV])
        assert binding.project_id == PROJECT_ID
        assert binding.worktree_id == WORKTREE_ID
        assert binding.canonical_task_id == task_id
        assert binding.handoff.work_role.value == "coder"


# ---------------------------------------------------------------------------
# R4 — grounded durable handoff identity is preserved
# ---------------------------------------------------------------------------


class TestR4GroundedHandoffPreserved:
    def test_worker_envelope_tied_to_same_grounded_durable_handoff(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)

        semantic = {
            "work_role": "coder",
            "task_kind": "af53-m3w2r1-grounded",
            "objective": "grounded thin Worker binding propagation",
            "bounded_scope": "bounded grounded scope",
            "validation_expectations": ["focused grounding validation"],
            "semantic_stop_expectations": ["stop on identity mismatch"],
        }
        grounded = handoff_write(
            mode="work_item",
            semantic=semantic,
            caller_role="task-main",
            sandbox=host.sandbox,
            plan_ref=PLAN_AUTH,
            milestone_id="M1",
            work_item_id="W1",
            provenance={"plan_digest": PLAN_DIGEST, "grounding": "task_main_authoritative_work_source"},
        )
        started = _start(host, grounded.ref)
        assert started.get("is_success"), started
        assert started["payload"]["handoff_digest"] == grounded.digest

        # Independent re-open of the SAME durable handoff: the envelope's
        # TaskHandoff derivation must match the canonical durable handoff.
        from aota_forge.work_plane.handoff_store import handoff_open
        from aota_forge.work_plane.task_facade import (
            load_trusted_work_item_task_handoff,
        )

        opened = handoff_open(grounded.ref, "full", sandbox=host.sandbox)
        assert opened["digest"] == grounded.digest
        derived = load_trusted_work_item_task_handoff(
            opened=opened, sandbox=host.sandbox
        )

        child = _child_env_from_dispatch(recorder)
        kind, payload = verify_envelope(child[PRE_RESOLVED_BINDING_ENV])
        assert kind == "worker"
        assert payload["handoff_digest"] == derived.handoff_digest
        handoff = TaskHandoff.from_dict(payload["handoff"])
        assert handoff.handoff_digest == payload["handoff_digest"]
        assert handoff.work_role.value == "coder"
        assert handoff.objective == semantic["objective"]
        assert handoff.bounded_scope == semantic["bounded_scope"]
        assert handoff.project_ref is not None and handoff.project_ref.ref == PROJECT_ID
        assert handoff.milestone_ref is not None and handoff.milestone_ref.ref == "M1"
        assert handoff.work_item_ref is not None and handoff.work_item_ref.ref == "W1"
        assert handoff.plan_ref is not None and handoff.plan_ref.ref == PLAN_AUTH
        assert handoff.plan_ref.digest == PLAN_DIGEST

        durable_path = host.worktree_root / ".aota" / "handoffs" / f"{grounded.digest}.json"
        durable = json.loads(durable_path.read_text(encoding="utf-8"))
        assert durable["digest"] == grounded.digest
        assert durable["semantic"]["objective"] == semantic["objective"]
        assert durable["envelope"]["work_item_id"] == "W1"
        assert durable["envelope"]["milestone_id"] == "M1"

        binding = load_binding_from_envelope(child[PRE_RESOLVED_BINDING_ENV])
        assert binding.handoff.handoff_digest == derived.handoff_digest


# ---------------------------------------------------------------------------
# R5 — role mismatch remains fail-closed before dispatch
# ---------------------------------------------------------------------------


class TestR5RoleMismatchFailClosed:
    def test_requested_role_must_equal_grounded_handoff_role(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)

        ref = _write_work_item(host, role="coder")
        response = _start(host, ref, role="reviewer")
        assert response.get("is_success") is False
        assert (response.get("error") or {}).get("code") == "ROLE_HANDOFF_MISMATCH"
        assert recorder.calls == []
        assert host.execution_store.list_all() == []
        assert host.execution_dispatcher.list_routes() == []


# ---------------------------------------------------------------------------
# R6/R7 — governed dispatch without a valid resolver fails before spawn
# ---------------------------------------------------------------------------


class TestR6R7FailClosedBeforeSpawn:
    def test_direct_governed_dispatch_without_resolver_fails_before_spawn(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)
        client._worker_env_resolver = None

        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(_governed_payload())
        assert excinfo.value.code == "DISPATCH_REJECTED"
        assert recorder.calls == []

    def test_composed_task_start_without_resolver_fails_before_spawn(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)
        client._worker_env_resolver = None

        ref = _write_work_item(host)
        response = _start(host, ref)
        assert response.get("is_success") is False
        assert recorder.calls == []
        assert host.execution_dispatcher.list_routes() == []
        records = host.execution_store.list_all()
        assert records, "durable intent record expected for honest crash-window truth"
        for record in records:
            assert record.execution_phase == ExecutionPhase.PREPARED
            assert record.adapter_handle is None

    def test_resolver_returning_none_fails_before_spawn(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)
        client._worker_env_resolver = lambda _payload: None

        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(_governed_payload())
        assert excinfo.value.code == "DISPATCH_REJECTED"
        assert recorder.calls == []

    def test_resolver_returning_non_mapping_fails_before_spawn(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)
        client._worker_env_resolver = lambda _payload: "not-a-mapping"  # type: ignore[return-value]

        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(_governed_payload())
        assert excinfo.value.code == "PACKAGE_INVALID"
        assert recorder.calls == []

    def test_resolver_without_envelope_fails_before_spawn(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)
        client._worker_env_resolver = lambda _payload: {"PYTHONPATH": str(REPO_ROOT)}

        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(_governed_payload())
        assert excinfo.value.code == "DISPATCH_REJECTED"
        assert recorder.calls == []


# ---------------------------------------------------------------------------
# R8 — foreign project / worktree handoffs fail closed
# ---------------------------------------------------------------------------


class TestR8ForeignIdentityFailClosed:
    @staticmethod
    def _foreign_handoff(
        tmp_path: Path,
        *,
        project_id: str = "foreign_project",
        worktree_id: str = "wt-foreign",
    ) -> tuple[Any, Path]:
        root = tmp_path / f"foreign-{project_id}-{worktree_id}"
        (root / ".aota").mkdir(parents=True, exist_ok=True)
        (root / ".aota" / "project.yaml").write_text(
            PROJECT_MANIFEST.format(project_id=project_id), encoding="utf-8"
        )
        sandbox = WorktreeSandboxBoundary(
            workspace_id="ws-foreign",
            workspace_root=str(root),
            project_id=project_id,
            project_root=str(root),
            worktree_id=worktree_id,
            worktree_root=str(root.resolve()),
            registry_fingerprint="0" * 64,
            candidate_fingerprint="1" * 64,
        )
        ref = handoff_write(
            mode="work_item",
            semantic={
                "work_role": "coder",
                "task_kind": "foreign",
                "objective": "foreign objective",
                "bounded_scope": "foreign bounded scope",
                "validation_expectations": ["v"],
                "semantic_stop_expectations": ["stop"],
            },
            caller_role="task-main",
            sandbox=sandbox,
        )
        return ref, root

    def test_foreign_project_handoff_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)

        foreign, foreign_root = self._foreign_handoff(tmp_path, project_id="foreign_project")
        (host.worktree_root / ".aota" / "handoffs").mkdir(parents=True, exist_ok=True)
        shutil.copy(
            foreign_root / ".aota" / "handoffs" / f"{foreign.digest}.json",
            host.worktree_root / ".aota" / "handoffs" / f"{foreign.digest}.json",
        )
        payload = _governed_payload(ref=foreign.ref, digest=foreign.digest)
        with pytest.raises(TrustedBindingError) as resolver_exc:
            client._worker_env_resolver(payload)
        assert resolver_exc.value.code == "WORKER_BINDING_UNAVAILABLE"
        assert "cross-project" in str(resolver_exc.value)
        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(payload)
        assert excinfo.value.code == "WORKER_BINDING_UNAVAILABLE"
        assert recorder.calls == []

    def test_foreign_worktree_handoff_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)

        foreign, foreign_root = self._foreign_handoff(
            tmp_path, project_id=PROJECT_ID, worktree_id="wt-foreign"
        )
        (host.worktree_root / ".aota" / "handoffs").mkdir(parents=True, exist_ok=True)
        shutil.copy(
            foreign_root / ".aota" / "handoffs" / f"{foreign.digest}.json",
            host.worktree_root / ".aota" / "handoffs" / f"{foreign.digest}.json",
        )
        payload = _governed_payload(ref=foreign.ref, digest=foreign.digest)
        with pytest.raises(TrustedBindingError) as resolver_exc:
            client._worker_env_resolver(payload)
        assert resolver_exc.value.code == "WORKER_BINDING_UNAVAILABLE"
        assert "cross-worktree" in str(resolver_exc.value)
        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(payload)
        assert excinfo.value.code == "WORKER_BINDING_UNAVAILABLE"
        assert recorder.calls == []

    def test_tampered_handoff_fails_closed(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)
        client = _real_host_client(host)

        grounded = handoff_write(
            mode="work_item",
            semantic={
                "work_role": "coder",
                "task_kind": "tamper",
                "objective": "tamper target",
                "bounded_scope": "bounded",
                "validation_expectations": ["v"],
                "semantic_stop_expectations": ["stop"],
            },
            caller_role="task-main",
            sandbox=host.sandbox,
        )
        artifact = host.worktree_root / ".aota" / "handoffs" / f"{grounded.digest}.json"
        data = json.loads(artifact.read_text(encoding="utf-8"))
        data["semantic"]["objective"] = "tampered by an attacker"
        artifact.write_text(json.dumps(data), encoding="utf-8")

        payload = _governed_payload(ref=grounded.ref, digest=grounded.digest)
        with pytest.raises(TrustedBindingError) as resolver_exc:
            client._worker_env_resolver(payload)
        assert resolver_exc.value.code == "WORKER_BINDING_UNAVAILABLE"
        assert "tamper" in str(resolver_exc.value).lower()
        with pytest.raises(HermesHostClientError) as excinfo:
            client.dispatch(payload)
        assert excinfo.value.code == "WORKER_BINDING_UNAVAILABLE"
        assert recorder.calls == []


# ---------------------------------------------------------------------------
# R9 — fresh-process thin production path stays legacy-free
# ---------------------------------------------------------------------------


_FRESH_THIN_WORKER_SCRIPT = """
import json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host

td = Path(tempfile.mkdtemp(prefix="af53-m3w2r1-fresh-"))
root = td / "wt"
root.mkdir()
(root / ".aota").mkdir()
(root / ".aota" / "project.yaml").write_text({manifest!r}, encoding="utf-8")
exe = td / "hermes-stub"
exe.write_text("#!/bin/sh\\nexit 0\\n")
exe.chmod(0o755)
cfg = td / "runtime.json"
cfg.write_text(json.dumps({{
    "executor": "hermes",
    "executable": str(exe),
    "concurrency": 1,
    "provider": "test-provider",
    "model": "test-model",
    "runtime_path": "thin",
    "bindings": {{
        "analyst": {{"profile": "aota-worker", "toolsets": ["aota"]}},
        "coder": {{"profile": "aota-worker", "toolsets": ["aota"]}},
        "reviewer": {{"profile": "aota-worker", "toolsets": ["aota"]}},
        "project-steward": {{"profile": "aota-worker", "toolsets": ["aota"]}},
        "task-main": {{"profile": "aota-task-main"}},
    }},
}}), encoding="utf-8")

class FakePopen:
    def __init__(self, *args, **kwargs):
        self.pid = 424242
    def poll(self):
        return None

host = compose_thin_task_main_host(
    worktree_root=root,
    project_id="aota_forge",
    worktree_id="wt-fresh",
    runtime_config_path=cfg,
    origin_task_main_session_ref="20260913_af53_m3w2r1_fresh_session",
)
adapter = list(host.execution_dispatcher.registry._adapters.values())[0]
client = adapter._host_client
client._popen_factory = FakePopen
client.runtime_root = td / "runs"
client.runtime_root.mkdir()
print("RESOLVER=%s" % ("present" if client._worker_env_resolver is not None else "missing"))
written = host.invoke("handoff.write", {{"mode": "work_item", "payload": {{
    "work_role": "coder", "task_kind": "fresh", "objective": "child",
    # AF #54 M2/W2: explicit Plan/Work semantic identity (task-main owned).
    "work_item_ref": "W1",
    "milestone_ref": "M1",
    "bounded_scope": "bounded", "validation_expectations": ["focused"],
    "semantic_stop_expectations": ["stop"],
}}}})
started = host.invoke("task.start", {{"role": "coder", "handoff_ref": written["payload"]["ref"]}})
print("TASK_START_OK" if started.get("is_success") else "TASK_START_BAD:" + str(started.get("error")))
loaded = sorted(
    name
    for name in sys.modules
    if name == "aota_forge.runtime.task_main"
    or name.startswith("aota_forge.runtime.task_main.")
)
print("LEGACY=" + ",".join(loaded))
for module in (
    "aota_forge.runtime.task_main.control",
    "aota_forge.runtime.task_main.coordinator",
    "aota_forge.runtime.task_main.reconciliation",
):
    print("%s=%s" % (module.rsplit(".", 1)[-1].upper(), "loaded" if module in sys.modules else "absent"))
"""


class TestR9ThinFreshProcessLegacyFree:
    def test_fresh_process_thin_worker_binding_path_loads_no_legacy_modules(self) -> None:
        script = _FRESH_THIN_WORKER_SCRIPT.format(
            manifest=PROJECT_MANIFEST.format(project_id="aota_forge")
        )
        child_env = {
            k: v for k, v in os.environ.items() if not k.startswith("AOTA_")
        }
        child_env["PYTHONPATH"] = str(REPO_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        lines = completed.stdout.splitlines()
        assert "RESOLVER=present" in lines
        assert "TASK_START_OK" in lines
        legacy_lines = [line for line in lines if line.startswith("LEGACY=")]
        assert len(legacy_lines) == 1
        assert legacy_lines[0] == "LEGACY=", (
            "thin Worker binding path pulled in legacy workflow state: " + legacy_lines[0]
        )
        assert "CONTROL=absent" in lines
        assert "COORDINATOR=absent" in lines
        assert "RECONCILIATION=absent" in lines


# ---------------------------------------------------------------------------
# R10 — legacy Worker propagation path preserved (accepted suites rerun)
# ---------------------------------------------------------------------------


class TestR10LegacyWorkerPathPreserved:
    def test_legacy_bootstrap_still_owns_its_worker_env_resolver(self) -> None:
        from aota_forge.composition import task_main_host_bootstrap

        assert callable(task_main_host_bootstrap.try_build_task_main_binding)
        assert callable(task_main_host_bootstrap.write_bootstrap_file)
        source = (
            REPO_ROOT / "aota_forge" / "composition" / "task_main_host_bootstrap.py"
        ).read_text(encoding="utf-8")
        assert "_w2_worker_env_resolver" in source
        assert "build_worker_child_environment" in source
        # The shared neutral primitive is the same canonical builder used by the
        # thin path; no legacy workflow semantics were refactored.
        assert callable(wvs.build_worker_child_environment)

    def test_thin_module_does_not_import_legacy_bootstrap(self) -> None:
        source = (
            REPO_ROOT / "aota_forge" / "composition" / "thin_task_main_host.py"
        ).read_text(encoding="utf-8")
        assert "task_main_host_bootstrap" not in source


# ---------------------------------------------------------------------------
# R11 — one canonical aota.invoke Worker MCP plane
# ---------------------------------------------------------------------------


class TestR11SameCanonicalAotaInvoke:
    def test_worker_mcp_child_exposes_exactly_one_aota_invoke(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(tmp_path, test_hermes_executable)
        host = _compose(tmp_path, cfg)
        recorder, _runs = _arm_recorder(host, tmp_path)

        ref = _write_work_item(host)
        started = _start(host, ref)
        assert started.get("is_success"), started

        child = _child_env_from_dispatch(recorder)
        tools = asyncio.run(
            _mcp_child_tool_names(child, cwd=str(host.worktree_root))
        )
        assert tools == ["aota.invoke"]
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)


# ---------------------------------------------------------------------------
# R12 — trusted 900s Worker execution policy preserved
# ---------------------------------------------------------------------------


class TestR12TimeoutPolicyPreserved:
    def test_trusted_900s_worker_execution_policy_reaches_run_spec(
        self, tmp_path: Path, test_hermes_executable: Path
    ) -> None:
        cfg = _write_runtime_config(
            tmp_path, test_hermes_executable, timeout_seconds=900
        )
        host = _compose(tmp_path, cfg)
        client = _real_host_client(host)
        assert DEFAULT_WORKER_EXECUTION_TIMEOUT_SECONDS == 900
        assert client.timeout_seconds == 900

        recorder, runs = _arm_recorder(host, tmp_path)
        ref = _write_work_item(host)
        started = _start(host, ref)
        assert started.get("is_success"), started
        assert len(recorder.calls) == 1

        run_dirs = list((runs / "runs").iterdir())
        assert len(run_dirs) == 1
        spec = json.loads((run_dirs[0] / "spec.json").read_text(encoding="utf-8"))
        assert spec["timeout_seconds"] == 900
