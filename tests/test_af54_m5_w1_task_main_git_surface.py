"""AF #54 M5/W1 — task-main AF Git lifecycle production convergence.

PROVES (focused, deterministic, no network, no real Hermes):

* the production thin task-main binding carries REAL trusted git authorities:
  git.status / git.diff succeed through the canonical single-entry
  ``aota.invoke`` dispatch (the historical seam where these operations
  existed but were UNREACHABLE because no production role authority minted a
  usable git authority for task-main);
* exposure is not authority: lifecycle mutation operations may be VISIBLE on
  the surface while the binding carries no lifecycle authority — the call
  still fails closed with AUTHORITY_DENIED;
* the bounded mutation family (git.checkpoint / git.integrate / git.push) is
  minted ONLY for the trusted task-main binding and ONLY when the operator
  mechanically configured the integration branch / remote;
* checkpoint CAS (expected_head), FF-only integration with expected-old CAS,
  and expected-remote CAS push behave fail-closed exactly as governed;
* worker role bindings keep zero git authorities (not accidentally widened);
* no caller-supplied repo path / arbitrary argv / destructive verb can reach
  the provider.

DOES_NOT_PROVE: a real Hermes session (that is M5/W4).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.core.providers.tool import ToolRequest
from aota_forge.composition.thin_task_main_host import (
    THIN_TASK_MAIN_EAGER_OPERATIONS,
    THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS,
    ThinTaskMainHost,
    compose_thin_task_main_host,
)
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedBindingError,
    TrustedWorkerBinding,
)
from aota_forge.work_plane.git_tools import (
    GIT_CHECKPOINT_DESCRIPTOR,
    GIT_DIFF_DESCRIPTOR,
    GIT_INTEGRATE_DESCRIPTOR,
    GIT_PUSH_DESCRIPTOR,
    GIT_STATUS_DESCRIPTOR,
    BoundedGitToolProvider,
    GitAuthorityError,
    create_git_authority,
)
from aota_forge.work_plane.handoff import TaskHandoff

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


class _FakeHostClient:
    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def dispatch(self, payload: Any) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "adapter_handle": f"fake-{len(self.payloads)}",
            "status": "running",
            "dispatch_time": "2026-09-14T00:00:00Z",
        }


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, timeout=30
    )
    return proc.stdout.strip()


def _make_git_worktree(tmp_path: Path, project_id: str = "af54_m5w1") -> Path:
    root = tmp_path / f"wt-{project_id}"
    root.mkdir(parents=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "af54-m5w1@test.local")
    _git(root, "config", "user.name", "af54-m5w1")
    (root / ".aota").mkdir()
    (root / ".aota" / "project.yaml").write_text(
        PROJECT_MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    (root / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    return root


def _runtime_config(tmp_path: Path) -> Path:
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    cfg = tmp_path / "runtime.json"
    cfg.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": str(exe),
                "concurrency": 1,
                "provider": "test-provider",
                "model": "test-model",
                "bindings": {
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "task-main": {"profile": "aota-task-main"},
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    yield
    reset_execution_dispatcher()


def _compose(
    tmp_path: Path,
    root: Path,
    *,
    project_id: str = "af54_m5w1",
    worktree_id: str = "m5w1-wt",
    git_integration_branch: str | None = None,
    git_remote: str | None = None,
) -> ThinTaskMainHost:
    return compose_thin_task_main_host(
        worktree_root=root,
        project_id=project_id,
        worktree_id=worktree_id,
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260914_af54_m5w1_trusted_session",
        host_client=_FakeHostClient(),
        git_integration_branch=git_integration_branch,
        git_remote=git_remote,
    )


def _err_code(resp: dict[str, Any]) -> str:
    return str((resp.get("error") or {}).get("code", ""))


# ---------------------------------------------------------------------------
# Production read authority (the historical gap: known ops, no usable mint)
# ---------------------------------------------------------------------------


class TestW1ReadAuthorityProductionWiring:
    def test_task_main_binding_carries_git_read_authorities(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        names = {a.operation.name for a in host.trusted_binding.git_authorities}
        assert {"git.status", "git.diff"} <= names
        # unconfigured lifecycle => no mutation authority minted
        assert "git.checkpoint" not in names

    def test_real_aota_invoke_git_status_pass(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        (root / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
        resp = host.invoke("git.status", {})
        assert resp.get("is_success") is True, resp
        payload = resp.get("payload") or {}
        assert payload.get("project_id") == "af54_m5w1"
        assert payload.get("worktree_id") == "m5w1-wt"

    def test_real_aota_invoke_git_diff_pass(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        (root / "tracked.py").write_text("VALUE = 3\n", encoding="utf-8")
        resp = host.invoke("git.diff", {})
        assert resp.get("is_success") is True, resp
        paths = [e["path"] for e in (resp.get("payload") or {}).get("entries", [])]
        assert "tracked.py" in paths

    def test_exposure_is_not_authority_lifecycle_denied(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        surface = host.trusted_binding.tool_surface
        visible = set(surface.all_capability_names())
        assert {"git.checkpoint", "git.integrate", "git.push"} <= visible
        assert "git.status" in THIN_TASK_MAIN_EAGER_OPERATIONS
        assert "git.checkpoint" in THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS
        resp = host.invoke("git.checkpoint", {"message": "should be denied"})
        assert resp.get("is_success") is False
        assert _err_code(resp) == "AUTHORITY_DENIED"

    def test_caller_arbitrary_repo_path_denied(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        resp = host.invoke("git.status", {"repo_path": "/home/other/project"})
        assert resp.get("is_success") is False
        assert _err_code(resp) in ("UNKNOWN_INPUT", "INPUT_TYPE_INVALID")

    def test_destructive_names_unreachable(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        for bad in (
            "git.reset_hard", "git.clean", "git.force_push", "git.rebase",
            "git.exec", "git.branch_delete", "git.remote_add",
        ):
            resp = host.invoke(bad, {})
            assert resp.get("is_success") is False
            assert _err_code(resp) == "UNKNOWN_OPERATION", bad


# ---------------------------------------------------------------------------
# Lifecycle mutation: checkpoint / integrate / push, all fail-closed governed
# ---------------------------------------------------------------------------


class TestW1LifecycleMutationSurface:
    def _configured(self, tmp_path: Path) -> tuple[ThinTaskMainHost, Path]:
        root = _make_git_worktree(tmp_path)
        # origin remote (local bare repo — no network)
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "--bare", str(origin)], check=True, timeout=30)
        _git(root, "remote", "add", "origin", str(origin))
        _git(root, "push", "origin", "main")
        # task-main works on its own branch
        _git(root, "checkout", "-b", "aota/m5w1/work")
        host = _compose(
            tmp_path, root, git_integration_branch="main", git_remote="origin"
        )
        return host, root

    def test_checkpoint_requires_task_main_trusted_authority(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        # Build a real sandbox by composing once, then re-mint under a worker handoff.
        host = _compose(tmp_path, root)
        trusted = host.trusted_binding.sandbox
        coder_handoff = TaskHandoff(
            work_role="coder",
            task_kind="worker",
            objective="worker",
            bounded_scope="worker scope",
            validation_expectations=("x",),
            semantic_stop_expectations=("stop",),
        )
        with pytest.raises(GitAuthorityError):
            create_git_authority(trusted, coder_handoff, (), GIT_CHECKPOINT_DESCRIPTOR)
        with pytest.raises(GitAuthorityError):
            create_git_authority(trusted, coder_handoff, (), GIT_INTEGRATE_DESCRIPTOR)
        with pytest.raises(GitAuthorityError):
            create_git_authority(trusted, coder_handoff, (), GIT_PUSH_DESCRIPTOR)

    def test_worker_binding_has_zero_git_authorities(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        from aota_forge.work_plane.af_roles import get_tool_surface_for_role

        worker_handoff = TaskHandoff(
            work_role="coder",
            task_kind="worker",
            objective="implement",
            bounded_scope="only src",
            validation_expectations=("pytest",),
            semantic_stop_expectations=("stop",),
        )
        binding = TrustedWorkerBinding(
            canonical_task_id="af54_m5w1:task:t1",
            project_id="af54_m5w1",
            worktree_id="m5w1-wt",
            trusted_context=host.trusted_binding.trusted_context,
            handoff=worker_handoff,
            sandbox=host.trusted_binding.sandbox,
            tool_surface=get_tool_surface_for_role("coder"),
            read_authorities=(),
        )
        assert binding.git_authorities == ()

    def test_binding_rejects_foreign_sandbox_git_authority(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root)
        other_root = _make_git_worktree(tmp_path, project_id="other_proj")
        other = _compose(tmp_path, other_root, project_id="other_proj", worktree_id="other-wt")
        with pytest.raises(TrustedBindingError):
            TrustedWorkerBinding(
                canonical_task_id=host.trusted_binding.canonical_task_id,
                project_id="af54_m5w1",
                worktree_id="m5w1-wt",
                trusted_context=host.trusted_binding.trusted_context,
                handoff=host.trusted_binding.handoff,
                sandbox=host.trusted_binding.sandbox,
                tool_surface=host.trusted_binding.tool_surface,
                read_authorities=host.trusted_binding.read_authorities,
                git_authorities=other.trusted_binding.git_authorities,
            )

    def test_checkpoint_cas_and_success(self, tmp_path: Path) -> None:
        host, root = self._configured(tmp_path)
        (root / "tracked.py").write_text("VALUE = 42\n", encoding="utf-8")
        (root / "new_file.py").write_text("X = 1\n", encoding="utf-8")
        head = _git(root, "rev-parse", "HEAD")
        stale = host.invoke("git.checkpoint", {"message": "wip", "expected_head": "0" * 40})
        assert stale.get("is_success") is False
        assert _err_code(stale) == "GIT_CAS_CONFLICT"
        ok = host.invoke("git.checkpoint", {"message": "accept W1 work", "expected_head": head})
        assert ok.get("is_success") is True, ok
        payload = ok.get("payload") or {}
        new_head = _git(root, "rev-parse", "HEAD")
        assert payload.get("commit_sha") == new_head
        assert payload.get("parent_sha") == head
        assert "accept W1 work" in _git(root, "log", "-1", "--pretty=%B")
        # runtime state under .aota is never committed
        committed = _git(root, "show", "--name-only", "--pretty=", "HEAD").splitlines()
        assert not any(line.startswith(".aota/") for line in committed)

    def test_checkpoint_nothing_to_commit_fails_closed(self, tmp_path: Path) -> None:
        host, _root = self._configured(tmp_path)
        resp = host.invoke("git.checkpoint", {"message": "empty"})
        assert resp.get("is_success") is False
        assert _err_code(resp) == "GIT_NOTHING_TO_COMMIT"

    def test_integrate_expected_old_cas_and_ff_only(self, tmp_path: Path) -> None:
        host, root = self._configured(tmp_path)
        (root / "tracked.py").write_text("VALUE = 7\n", encoding="utf-8")
        ck = host.invoke("git.checkpoint", {"message": "accepted work"})
        assert ck.get("is_success") is True
        target = (ck.get("payload") or {})["commit_sha"]
        base = (ck.get("payload") or {})["parent_sha"]
        wrong = host.invoke("git.integrate", {"expected_old_sha": "1" * 40})
        assert wrong.get("is_success") is False
        assert _err_code(wrong) == "GIT_CAS_CONFLICT"
        ok = host.invoke("git.integrate", {"expected_old_sha": base})
        assert ok.get("is_success") is True, ok
        assert _git(root, "rev-parse", "refs/heads/main") == target
        # non-ff: diverged main must be rejected, never merged or forced
        _git(root, "checkout", "main")
        (root / "side.py").write_text("S = 1\n", encoding="utf-8")
        _git(root, "add", "side.py")
        _git(root, "commit", "-m", "divergent main")
        diverged_main = _git(root, "rev-parse", "refs/heads/main")
        _git(root, "checkout", "aota/m5w1/work")
        (root / "tracked.py").write_text("VALUE = 8\n", encoding="utf-8")
        ck2 = host.invoke("git.checkpoint", {"message": "work 2"})
        assert ck2.get("is_success") is True
        resp = host.invoke("git.integrate", {"expected_old_sha": diverged_main})
        assert resp.get("is_success") is False
        assert _err_code(resp) == "GIT_NON_FAST_FORWARD"

    def test_push_expected_remote_cas_ff_only_sync(self, tmp_path: Path) -> None:
        host, root = self._configured(tmp_path)
        (root / "tracked.py").write_text("VALUE = 9\n", encoding="utf-8")
        ck = host.invoke("git.checkpoint", {"message": "frontier"})
        target = (ck.get("payload") or {})["commit_sha"]
        base = (ck.get("payload") or {})["parent_sha"]
        integ = host.invoke("git.integrate", {"expected_old_sha": base})
        assert integ.get("is_success") is True
        wrong = host.invoke("git.push", {"expected_remote_sha": "2" * 40})
        assert wrong.get("is_success") is False
        assert _err_code(wrong) == "GIT_REMOTE_STALE"
        ok = host.invoke("git.push", {})
        assert ok.get("is_success") is True, ok
        origin = tmp_path / "origin.git"
        remote_sha = subprocess.run(
            ["git", "ls-remote", str(origin), "refs/heads/main"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.split()[0]
        assert remote_sha == target
        # idempotent re-push: already synced, no second mutation
        again = host.invoke("git.push", {})
        assert again.get("is_success") is True
        assert (again.get("payload") or {}).get("already_synced") is True

    def test_mutation_provider_rejects_smuggled_repo_keys(self, tmp_path: Path) -> None:
        root = _make_git_worktree(tmp_path)
        host = _compose(tmp_path, root, git_integration_branch="main", git_remote="origin")
        auth = next(
            a for a in host.trusted_binding.git_authorities
            if a.operation.name == "git.checkpoint"
        )
        provider = BoundedGitToolProvider(auth)
        # canonical validation rejects unknown inputs (no caller repo path)
        with pytest.raises(Exception):
            ToolRequest(operation=GIT_CHECKPOINT_DESCRIPTOR, inputs={"message": "x", "repo_path": "/tmp/elsewhere"})
        # defensive provider-level rejection of smuggled keys
        request = ToolRequest(operation=GIT_CHECKPOINT_DESCRIPTOR, inputs={"message": "x"})
        object.__setattr__(request, "inputs", {"message": "x", "force": True})
        resp = provider.invoke(request)
        assert resp.ok is False
        assert resp.error["code"] == "FORBIDDEN_INPUT"
