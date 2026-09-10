"""AF #46 M2/W2-R1 — Pre-resolved Binding Host Forwarding Repair.

Proves I46-B001 repair: AF runtime composition already creates the
signed/digest-bound pre-resolved binding envelope and exports the opaque
locator (AOTA_PRE_RESOLVED_BINDING), and the Hermes host/profile channel now
mechanically forwards that locator into the MCP child. The locator is
transport, never authority:

    AOTA_PRE_RESOLVED_BINDING_IS_TRANSPORT_LOCATOR=yes
    AOTA_PRE_RESOLVED_BINDING_IS_AUTHORITY_SOURCE=no
    HOST_ENV_IS_AUTHORITY_SOURCE=no

    HOST_FORWARDS_BINDING=yes / HOST_CREATES_BINDING=no
    HOST_INTERPRETS_BINDING=no / HOST_DECIDES_AUTHORITY=no

No real Hermes model/session run here: deterministic process/config proofs
only (REAL_HERMES_RETRY_IN_THIS_TASK=no).
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from aota_forge.composition import worker_vertical_slice as wvs
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_EXPLICIT_ENV,
    write_bootstrap_file,
)
from aota_forge.composition.worker_vertical_slice import MissingRuntimeContextError
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.trusted_runtime_binding import (
    PRE_RESOLVED_BINDING_ENV,
    TrustedBindingError,
    create_task_main_envelope,
    create_worker_envelope,
    verify_envelope,
)
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff_runtime import WorkSemanticProjection, resolve_bounded_work_handoff
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

# ---------------------------------------------------------------------------
# Fixture helpers (bounded, deterministic)
# ---------------------------------------------------------------------------
PROJECT_ID = "proj_m2w2r1"
WORKTREE_ID = "wt_m2w2r1"
MILESTONE = "M2"
WORK_ITEM = "W3"
PLAN_AUTH = "example-owner/example-m2w2r1#46"
PLAN_DIGEST = "e" * 64
SCOPE = "work/m2w2r1/bounded-scope"
VALIDATION = ("m2w2r1 validation",)
STOPS = ("stop if unclear",)

HERMES_TOOLS_ROOT = Path("/home/latios/workspace/aota-hermes-tools")
TASK_MAIN_PROFILE_SOURCE = HERMES_TOOLS_ROOT / "profiles" / "task-main" / "config.yaml"
WORKER_PROFILE_SOURCE = HERMES_TOOLS_ROOT / "profiles" / "aota-worker" / "config.yaml"

# Old production authority channel: proven dead after M2/W1 (production
# select_runtime_context uses the envelope only). Removed from the canonical
# production profile contract by this repair.
DEAD_AUTHORITY_KEYS = (
    "AOTA_W3_MCP_ROOT",
    "AOTA_W3_PROJECT_ID",
    "AOTA_W3_WORKTREE_ID",
    "AOTA_W3_TASK_ID",
    "AOTA_W3_HANDOFF_JSON",
)


def _view(milestone: str = MILESTONE, work_items: list[str] | None = None):
    work_items = work_items or [WORK_ITEM]
    graph = MilestoneWorkItemGraph(milestone_ref=milestone, work_items=work_items, dependencies=[])
    return MilestonePlanView(
        plan_authority=PLAN_AUTH,
        plan_digest=PLAN_DIGEST,
        plan_source_revision="rev-m2w2r1",
        milestone_id=milestone,
        entry_base="b" * 40,
        graph=graph,
        milestone_user_approval_satisfied=True,
    )


def _projection():
    return WorkSemanticProjection.from_dict(
        {"objective": "m2w2r1 objective", "bounded_scope": SCOPE, "validation_expectations": list(VALIDATION), "semantic_stop_expectations": list(STOPS)}
    )


def _handoff(role: str = "coder", work_item: str = WORK_ITEM, milestone: str = MILESTONE, project_id: str = PROJECT_ID):
    return resolve_bounded_work_handoff(
        work_item_id=work_item, milestone_ref=milestone, projection=_projection(), project_id=project_id, plan_authority=PLAN_AUTH, plan_digest=PLAN_DIGEST
    )


def _sandbox(root: Path, project_id: str = PROJECT_ID, worktree_id: str = WORKTREE_ID):
    cand = ProjectCandidateEvidence(
        workspace_id="ws-m2w2r1", workspace_root=str(root), project_id=project_id, project_root=str(root),
        manifest_path="manifest.json", name="m2w2r1", kind="project", status="active",
        registry_fingerprint="a" * 64, candidate_fingerprint="b" * 64,
    )
    ev = ProjectResolutionEvidence(
        status="RESOLVED", workspace_id="ws-m2w2r1", workspace_root=str(root),
        registry_fingerprint="a" * 64, listing_fingerprint="c" * 64, candidates=(cand,),
    )
    return bind_worktree_sandbox(ev, worktree_id, root)


def _make_project(tmp: Path, project_id: str = PROJECT_ID):
    root = tmp / f"wt-{project_id}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        f"schema_version: 1\nproject:\n  id: {project_id}\n  name: t\n  kind: test\n  status: active\n"
        f"summary: test project\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n  docs: []\n"
        f"  scripts: []\n  profiles: []\n  skills: []\n  tests: []\ncommands:\n  validate: []\n  deploy: []\n"
        f"  verify_deploy: []\nruntime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        f"codegraph:\n  enabled: false\n  index_location: .codegraph\nplan:\n  active_plan_id: null\nconstraints: []\n",
        encoding="utf-8",
    )
    return root


def _runtime_config_json(path: Path):
    path.write_text(json.dumps({"executor": "hermes", "executable": "/bin/false", "concurrency": 2, "provider": "opencode-go", "model": "m", "bindings": {"task-main": {"profile": "aota-task-main"}, "coder": {"profile": "aota-worker", "toolsets": ["aota"]}, "analyst": {"profile": "aota-worker", "toolsets": ["aota"]}, "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]}, "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]}}}), encoding="utf-8")


def _write_task_main_bootstrap(root: Path, tmp: Path, project_id: str = PROJECT_ID):
    coord = root / ".aota" / "coordinator.json"
    execp = root / ".aota" / "execution.json"
    coord.write_text("{}", encoding="utf-8")
    execp.write_text("{}", encoding="utf-8")
    cfg = tmp / f"rt-{project_id}.json"
    _runtime_config_json(cfg)
    bs = write_bootstrap_file(
        worktree_root=root, project_id=project_id, worktree_id=WORKTREE_ID,
        coordinator_store_path=coord, execution_store_path=execp, runtime_config_path=cfg,
        origin_task_main_session_ref="sess-m2w2r1", live_plan_view=_view(), next_milestone_view=None,
        work_semantics={WORK_ITEM: _projection().to_dict()},
    )
    assert bs.is_file()
    return bs, cfg


def _profile_mcp_env(path: Path) -> dict:
    assert path.is_file(), f"canonical Hermes profile source missing: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict) and "mcp_servers" in data
    server = data["mcp_servers"]["aota"]
    assert server.get("enabled") is True
    assert server.get("command") == "/usr/bin/python3"
    assert list(server.get("args", [])) == ["-m", "aota_forge.composition.worker_vertical_slice", "--mcp-server"]
    env = server.get("env")
    assert isinstance(env, dict)
    return dict(env)


def _hermes_interpolate(env_block: dict, process_env: dict) -> dict:
    """Emulate verified Hermes MCP env semantics (tools/mcp_tool_config.py):

    - ``${VAR}`` resolves from the Hermes process environment when set
      (single-profile ``hermes -p ... -z ...`` reads os.environ via
      get_secret fall-through when multiplexing is inactive);
    - an unset variable keeps the literal placeholder (never dropped, never
      special-cased);
    - the MCP child receives safe baseline + the interpolated server env
      block (never a raw copy of the parent environment).
    """
    pattern = re.compile(r"\$\{([^}]+)\}")

    def _resolve(value):
        if not isinstance(value, str):
            return value

        def _replace(m):
            name = m.group(1).strip()
            if name.startswith("env:"):
                name = name[len("env:"):].strip()
            return process_env.get(name, m.group(0))

        return pattern.sub(_replace, value)

    return {k: _resolve(v) for k, v in env_block.items()}


class _EnvGuard:
    def __init__(self):
        self.saved = dict(os.environ)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        os.environ.clear()
        os.environ.update(self.saved)

    def clear_aota(self):
        for k in list(os.environ.keys()):
            if k.startswith("AOTA_"):
                del os.environ[k]
        os.environ.pop("AOTA_ALLOW_LEGACY_ENV_DISCOVERY", None)
        os.environ.pop(PRE_RESOLVED_BINDING_ENV, None)


# ---------------------------------------------------------------------------
# 1. Canonical profile contract (both production profiles)
# ---------------------------------------------------------------------------
class TestCanonicalProfileContract:
    def test_task_main_profile_forwards_binding_locator(self):
        env = _profile_mcp_env(TASK_MAIN_PROFILE_SOURCE)
        # TASK_MAIN_PROFILE_FORWARDS_PRE_RESOLVED_BINDING=yes
        assert env.get(PRE_RESOLVED_BINDING_ENV) == "${AOTA_PRE_RESOLVED_BINDING}"
        # Mechanical entries preserved (bounded repair: PYTHONPATH untouched)
        assert env.get("PYTHONPATH") == "${AOTA_FORGE_REPO_ROOT}"

    def test_worker_profile_forwards_binding_locator(self):
        env = _profile_mcp_env(WORKER_PROFILE_SOURCE)
        # WORKER_PROFILE_FORWARDS_PRE_RESOLVED_BINDING=yes
        assert env.get(PRE_RESOLVED_BINDING_ENV) == "${AOTA_PRE_RESOLVED_BINDING}"
        assert env.get("PYTHONPATH") == "${AOTA_FORGE_REPO_ROOT}"

    def test_old_authority_channel_removed_from_both_profiles(self):
        for path in (TASK_MAIN_PROFILE_SOURCE, WORKER_PROFILE_SOURCE):
            env = _profile_mcp_env(path)
            for dead in DEAD_AUTHORITY_KEYS:
                assert dead not in env, f"{path.name} still forwards dead authority channel {dead}"
        # OLD_AOTA_W3_ENV_IS_PRODUCTION_AUTHORITY=no

    def test_no_placeholder_special_case_in_af_authority_path(self):
        # HERMES_PLACEHOLDER_FILTER_SPECIAL_CASE=no: AF never branches on
        # "${...}" literal shapes; placeholders are semantically irrelevant
        # and fail closed via missing/invalid envelope.
        roots = [
            Path("aota_forge/composition/worker_vertical_slice.py"),
            Path("aota_forge/composition/task_main_host_bootstrap.py"),
            Path("aota_forge/composition/task_main_daily_launcher.py"),
            Path("aota_forge/adapters/hermes/host_client.py"),
            Path("aota_forge/runtime/trusted_runtime_binding.py"),
            Path("aota_forge/mcp_transport.py"),
        ]
        needles = ('"${"', "'${'", "startswith(\"${", "startswith('${")
        for root in roots:
            text = root.read_text(encoding="utf-8")
            for needle in needles:
                assert needle not in text, f"{root} contains placeholder special-case {needle!r}"
        # PLACEHOLDER_SPECIAL_CASE_ADDED=no

    def test_hermes_interpolation_forwards_locator_when_launcher_sets_it(self):
        # Deterministic contract proof under verified Hermes semantics: the
        # launcher-supplied locator value flows through the profile block
        # into the MCP child env; nothing else authority-bearing can arrive.
        locator = "/tmp/fake-launch/.aota/pre-resolved-bindings/task-main-ab12cd34-ef56.json"
        for path in (TASK_MAIN_PROFILE_SOURCE, WORKER_PROFILE_SOURCE):
            env_block = _profile_mcp_env(path)
            child_env = _hermes_interpolate(env_block, {PRE_RESOLVED_BINDING_ENV: locator, "AOTA_FORGE_REPO_ROOT": "/repo"})
            assert child_env[PRE_RESOLVED_BINDING_ENV] == locator
            assert child_env["PYTHONPATH"] == "/repo"
            # Unset trace stays an inert literal (never authority)
            assert child_env.get("AOTA_W3_TOOL_TRACE") == "${AOTA_W3_TOOL_TRACE}"
            for dead in DEAD_AUTHORITY_KEYS:
                assert dead not in child_env

    def test_hermes_interpolation_keeps_literal_when_locator_unset(self):
        # Missing locator stays a literal placeholder in the child env, which
        # the MCP child treats as a non-file path and fails closed (no
        # silent authority, no special-case filtering).
        for path in (TASK_MAIN_PROFILE_SOURCE, WORKER_PROFILE_SOURCE):
            env_block = _profile_mcp_env(path)
            child_env = _hermes_interpolate(env_block, {"AOTA_FORGE_REPO_ROOT": "/repo"})
            assert child_env[PRE_RESOLVED_BINDING_ENV] == "${AOTA_PRE_RESOLVED_BINDING}"


# ---------------------------------------------------------------------------
# 2. Task-main path: launcher creates envelope, host forwards, child loads
# ---------------------------------------------------------------------------
class TestTaskMainProcessForwarding:
    def test_launcher_build_env_exports_task_main_locator(self, tmp_path: Path):
        from aota_forge.adapters.plan_authority import PlanAuthoritySnapshot
        from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher
        from aota_forge.composition.task_main_daily_launcher import DailyLaunchContext
        from aota_forge.runtime.config import load_runtime_config

        root = _make_project(tmp_path, PROJECT_ID)
        bs, cfg = _write_task_main_bootstrap(root, tmp_path)
        launcher = DailyTaskMainLauncher()
        ctx = DailyLaunchContext(
            worktree_root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
            runtime_config_path=cfg, coordinator_store_path=root / ".aota" / "coordinator.json",
            execution_store_path=root / ".aota" / "execution.json", live_plan_view=_view(),
            next_milestone_view=None, plan_snapshot=PlanAuthoritySnapshot(body="fixture", revision="r1"),
            runtime_config=load_runtime_config(config_path=str(cfg)), hermes_bin="/bin/false",
        )
        with _EnvGuard():
            env = launcher.build_env(ctx)
        # TASK_MAIN_BINDING_CREATED_BY_AF_RUNTIME=yes (envelope exists + verifies)
        locator = env.get(PRE_RESOLVED_BINDING_ENV)
        assert locator and Path(locator).is_file()
        kind, _payload = verify_envelope(locator)
        assert kind == "task-main"
        # TASK_MAIN_BINDING_FORWARDING_MECHANISM_PRESENT=yes: profile contract
        # forwards exactly this variable name.
        assert PRE_RESOLVED_BINDING_ENV in _profile_mcp_env(TASK_MAIN_PROFILE_SOURCE)
        # Task-main launch env carries no Worker authority keys (M1/W2-R1).
        for worker_key in ("AOTA_W3_MCP_ROOT", "AOTA_W3_PROJECT_ID", "AOTA_W3_WORKTREE_ID", "AOTA_W3_TASK_ID", "AOTA_W3_HANDOFF_JSON", "AOTA_W3_CONTEXT_KIND"):
            assert worker_key not in env
        assert "AOTA_TASK_MAIN_BOOTSTRAP" in env

    def test_task_main_envelope_loads_in_child_process(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        bs, _cfg = _write_task_main_bootstrap(root, tmp_path)
        envelope = create_task_main_envelope(worktree_root=root, bootstrap_path=bs)
        script = (
            "import os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            f"os.environ['{PRE_RESOLVED_BINDING_ENV}'] = {str(envelope)!r}\n"
            "from aota_forge.composition.worker_vertical_slice import select_runtime_context\n"
            "binding = select_runtime_context()\n"
            f"assert binding.project_id == {PROJECT_ID!r}, binding.project_id\n"
            "assert binding.handoff.work_role.value == 'task-main'\n"
            "assert binding.trusted_task_main_context is not None\n"
            "print('TASK_MAIN_CHILD_OK')\n"
        )
        repo_root = str(Path.cwd())
        child_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": repo_root}
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=repo_root, timeout=60, env=child_env)
        assert proc.returncode == 0, f"task-main child failed: {proc.stderr[-2000:]}"
        assert "TASK_MAIN_CHILD_OK" in proc.stdout
        # TASK_MAIN_MCP_CHILD_CAN_RECEIVE_BINDING_LOCATOR=yes


# ---------------------------------------------------------------------------
# 3. Worker path: composition creates envelope, host forwards, child loads
# ---------------------------------------------------------------------------
class TestWorkerProcessForwarding:
    def test_worker_child_env_builder_exports_locator(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        with _EnvGuard():
            child = wvs.build_worker_child_environment(
                root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
                canonical_task_id=f"{PROJECT_ID}:M2:W3:attempt-1", handoff=h,
            )
        # WORKER_BINDING_CREATED_BY_AF_RUNTIME=yes
        locator = child.get(PRE_RESOLVED_BINDING_ENV)
        assert locator and Path(locator).is_file()
        kind, payload = verify_envelope(locator)
        assert kind == "worker"
        assert payload["handoff_digest"] == h.handoff_digest
        # WORKER_BINDING_FORWARDING_MECHANISM_PRESENT=yes
        assert PRE_RESOLVED_BINDING_ENV in _profile_mcp_env(WORKER_PROFILE_SOURCE)

    def test_host_client_preserves_locator_into_supervisor_env(self, tmp_path: Path):
        from aota_forge.adapters.hermes.host_client import HermesHostClient

        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        overlay = wvs.build_worker_child_environment(
            root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
            canonical_task_id=f"{PROJECT_ID}:M2:W3:attempt-1", handoff=h,
        )
        launcher = tmp_path / "hermes-stub"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        runs = tmp_path / "runs"
        runs.mkdir()
        client = HermesHostClient(str(launcher), default_cwd=str(tmp_path), runtime_root=runs, worker_env_resolver=lambda _p: dict(overlay))
        payload = {
            "profile": "aota-worker", "instruction": "do bounded work", "package_id": "pkg-1",
            "operation": "task_dispatch", "artifacts": [], "result_expectations": {},
            "constraints": {}, "capability_requirements": {},
            "context": {"canonical_task_id": f"{PROJECT_ID}:M2:W3:attempt-1", "working_context": {}},
        }
        with _EnvGuard() as g:
            g.clear_aota()
            # Ambient task-main authority + stale worker keys must be stripped;
            # only the validated overlay (envelope + mechanics) is applied.
            os.environ["AOTA_TASK_MAIN_BOOTSTRAP"] = "/tmp/stale-bootstrap.json"
            os.environ["AOTA_W3_PROJECT_ID"] = "stale_proj"
            supervisor_env = client._build_explicit_supervisor_env(payload)
        # HOST_FORWARDS_BINDING=yes
        assert supervisor_env[PRE_RESOLVED_BINDING_ENV] == overlay[PRE_RESOLVED_BINDING_ENV]
        assert supervisor_env["AOTA_W3_PROJECT_ID"] == PROJECT_ID
        assert supervisor_env.get("AOTA_TASK_MAIN_BOOTSTRAP") is None
        # HOST_CREATES_BINDING=no / HOST_INTERPRETS_BINDING=no: host never
        # imports the trusted binding loader (mechanical existence check only).
        host_src = Path("aota_forge/adapters/hermes/host_client.py").read_text(encoding="utf-8")
        assert "trusted_runtime_binding" not in host_src
        assert "load_binding_from_envelope" not in host_src
        assert "verify_envelope" not in host_src

    def test_host_client_rejects_missing_envelope_file(self, tmp_path: Path):
        from aota_forge.adapters.hermes.host_client import HermesHostClient, HermesHostClientError

        launcher = tmp_path / "hermes-stub"
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        runs = tmp_path / "runs"
        runs.mkdir()
        bad_overlay = {PRE_RESOLVED_BINDING_ENV: str(tmp_path / "no-such-envelope.json"), "PYTHONPATH": "/repo"}
        client = HermesHostClient(str(launcher), default_cwd=str(tmp_path), runtime_root=runs, worker_env_resolver=lambda _p: dict(bad_overlay))
        payload = {
            "profile": "aota-worker", "instruction": "do bounded work", "package_id": "pkg-1",
            "operation": "task_dispatch", "artifacts": [], "result_expectations": {},
            "constraints": {}, "capability_requirements": {},
            "context": {"canonical_task_id": f"{PROJECT_ID}:M2:W3:attempt-1", "working_context": {}},
        }
        with _EnvGuard() as g:
            g.clear_aota()
            with pytest.raises(HermesHostClientError):
                client._build_explicit_supervisor_env(payload)

    def test_worker_envelope_loads_in_child_process(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        envelope = create_worker_envelope(
            worktree_root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
            canonical_task_id=f"{PROJECT_ID}:M2:W3:attempt-1", handoff=h,
        )
        script = (
            "import os, sys\n"
            "sys.path.insert(0, os.getcwd())\n"
            f"os.environ['{PRE_RESOLVED_BINDING_ENV}'] = {str(envelope)!r}\n"
            "from aota_forge.composition.worker_vertical_slice import select_runtime_context\n"
            "binding = select_runtime_context()\n"
            f"assert binding.project_id == {PROJECT_ID!r}, binding.project_id\n"
            f"assert binding.worktree_id == {WORKTREE_ID!r}\n"
            "assert binding.handoff.work_role.value == 'coder'\n"
            "print('WORKER_CHILD_OK')\n"
        )
        repo_root = str(Path.cwd())
        child_env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": repo_root}
        proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=repo_root, timeout=60, env=child_env)
        assert proc.returncode == 0, f"worker child failed: {proc.stderr[-2000:]}"
        assert "WORKER_CHILD_OK" in proc.stdout
        # WORKER_MCP_CHILD_CAN_RECEIVE_BINDING_LOCATOR=yes


# ---------------------------------------------------------------------------
# 4. Fail-closed behavior (missing / old-channel / placeholder / tamper)
# ---------------------------------------------------------------------------
class TestFailClosed:
    def test_missing_locator_fails_closed(self):
        with _EnvGuard() as g:
            g.clear_aota()
            with pytest.raises(MissingRuntimeContextError):
                wvs.select_runtime_context()
        # MISSING_BINDING_FAILS_CLOSED=yes

    def test_old_fields_alone_cannot_restore_authority(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        with _EnvGuard() as g:
            g.clear_aota()
            # Full legacy worker channel, no envelope: production fails closed.
            os.environ["AOTA_W3_MCP_ROOT"] = str(root)
            os.environ["AOTA_W3_PROJECT_ID"] = PROJECT_ID
            os.environ["AOTA_W3_WORKTREE_ID"] = WORKTREE_ID
            os.environ["AOTA_W3_TASK_ID"] = f"{PROJECT_ID}:M2:W3:attempt-1"
            os.environ["AOTA_W3_HANDOFF_JSON"] = json.dumps(h.to_dict(), sort_keys=True)
            with pytest.raises(MissingRuntimeContextError):
                wvs.select_runtime_context()
        # OLD_ENV_CHANNEL_FALLBACK_ON_MISSING_BINDING=no
        # LEGACY_DISCOVERY_AUTOMATIC_FALLBACK=no

    def test_placeholder_fields_cannot_affect_selection(self, tmp_path: Path):
        # With a valid envelope, placeholder/bogus host representation is
        # semantically irrelevant: authority comes from the verified envelope.
        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        envelope = create_worker_envelope(
            worktree_root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
            canonical_task_id=f"{PROJECT_ID}:M2:W3:attempt-1", handoff=h,
        )
        with _EnvGuard() as g:
            g.clear_aota()
            os.environ[PRE_RESOLVED_BINDING_ENV] = str(envelope)
            os.environ["AOTA_W3_MCP_ROOT"] = "${AOTA_W3_MCP_ROOT}"
            os.environ["AOTA_W3_PROJECT_ID"] = "${AOTA_W3_PROJECT_ID}"
            os.environ["AOTA_W3_WORKTREE_ID"] = "${AOTA_W3_WORKTREE_ID}"
            os.environ["AOTA_W3_TASK_ID"] = "${AOTA_W3_TASK_ID}"
            os.environ["AOTA_W3_HANDOFF_JSON"] = "${AOTA_W3_HANDOFF_JSON}"
            os.environ["AOTA_TASK_MAIN_BOOTSTRAP"] = "${AOTA_TASK_MAIN_BOOTSTRAP}"
            binding = wvs.select_runtime_context()
            assert binding.project_id == PROJECT_ID
            assert binding.handoff.work_role.value == "coder"
            assert binding.handoff.handoff_digest == h.handoff_digest

    def test_contradictory_env_claims_cannot_override_envelope(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        envelope = create_worker_envelope(
            worktree_root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
            canonical_task_id=f"{PROJECT_ID}:M2:W3:attempt-1", handoff=h,
        )
        with _EnvGuard() as g:
            g.clear_aota()
            os.environ[PRE_RESOLVED_BINDING_ENV] = str(envelope)
            os.environ["AOTA_W3_PROJECT_ID"] = "evil_proj"
            os.environ["AOTA_W3_WORKTREE_ID"] = "evil_wt"
            binding = wvs.select_runtime_context()
            assert binding.project_id == PROJECT_ID
            assert binding.worktree_id == WORKTREE_ID

    def test_tampered_worker_envelope_fails_closed(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        h = _handoff()
        envelope = create_worker_envelope(
            worktree_root=root, project_id=PROJECT_ID, worktree_id=WORKTREE_ID,
            canonical_task_id=f"{PROJECT_ID}:M2:W3:attempt-1", handoff=h,
        )
        data = json.loads(envelope.read_text(encoding="utf-8"))
        data["payload"]["project_id"] = "tampered_proj"
        envelope.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        with _EnvGuard() as g:
            g.clear_aota()
            os.environ[PRE_RESOLVED_BINDING_ENV] = str(envelope)
            with pytest.raises(TrustedBindingError):
                wvs.select_runtime_context()
        # HOST_MUTATES_BINDING_PAYLOAD=no / TAMPER fails closed (W1 preserved)

    def test_tampered_task_main_bootstrap_fails_closed(self, tmp_path: Path):
        root = _make_project(tmp_path, PROJECT_ID)
        bs, _cfg = _write_task_main_bootstrap(root, tmp_path)
        envelope = create_task_main_envelope(worktree_root=root, bootstrap_path=bs)
        # Swap the live bootstrap after envelope creation: digest mismatch.
        live = json.loads(bs.read_text(encoding="utf-8"))
        live["origin_task_main_session_ref"] = "tampered-session"
        bs.write_text(json.dumps(live, sort_keys=True), encoding="utf-8")
        with _EnvGuard() as g:
            g.clear_aota()
            os.environ[PRE_RESOLVED_BINDING_ENV] = str(envelope)
            with pytest.raises(TrustedBindingError):
                wvs.select_runtime_context()


# ---------------------------------------------------------------------------
# 5. Thin-host invariant (plumbing changed, semantics unchanged)
# ---------------------------------------------------------------------------
class TestThinHostInvariant:
    def test_hermes_remains_host_executor_adapter(self):
        src = Path("aota_forge/adapters/hermes/host_client.py").read_text(encoding="utf-8")
        assert "class HermesHostClient" in src
        # No authority semantics in the host layer.
        for needle in ("class PermissionEngine", "class AuthorityEngine", "decides_role", "decide_authority"):
            assert needle not in src
        # HERMES_IS_HOST_EXECUTOR_ADAPTER=yes / HERMES_IS_AF_AUTHORITY=no

    def test_semantic_owner_unchanged(self):
        from aota_forge.runtime import trusted_runtime_binding as trb

        assert trb.SEMANTIC_OWNER == "AF_CORE_OR_RUNTIME_COMPOSITION"
        assert trb.TRUSTED_RUNTIME_BINDING_OWNER == "AF_CORE_OR_RUNTIME_COMPOSITION"
        assert trb.HOST_ENV_IS_AUTHORITY_SOURCE is False
        assert trb.HERMES_PROFILE_IS_AUTHORITY_SOURCE is False
        assert trb.SERIALIZED_BINDING_IS_AUTHORITY_SOURCE is False
        # SEMANTIC_OWNER_CHANGED=no / AUTHORITY_OWNER_CHANGED=no

    def test_no_new_dispatch_plane_or_state_machine(self):
        from aota_forge import mcp_transport

        assert mcp_transport.MCP_PUBLIC_TOOLS == ("aota.invoke",)
        assert mcp_transport.ONE_SHARED_AOTA_MCP is True
        # NEW_DISPATCH_PLANE_CREATED=no / MCP_DISCOVERS_AUTHORITY=no
