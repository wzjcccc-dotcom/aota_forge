"""AF #54 M5/W2 — Agent-facing GitHub Issue operations (Plan-bound).

PROVES (focused, deterministic; gh transport faked at the seam):

* the four canonical operations exist as the ONLY model-visible GitHub
  surface: github.issue.read / github.issue.comments.read /
  github.issue.update / github.issue.comment.update, dispatched through the
  single aota.invoke entry;
* Plan identity (repo/owner/issue_number) is mechanically grounded from the
  trusted launch-time binding — the model never repeats owner/repo/issue and
  cannot redirect a call to a foreign repo/issue/comment;
* mutations are task-main-only (workers carry zero GitHub authority);
* stale writes are detected (Issue expected_updated_at CAS; comment expected
  digest CAS; marker section upsert is idempotent);
* generic github.api / raw gh remain unreachable to the model.

DOES_NOT_PROVE: live GitHub (that is M5/W4 real Hermes E2E).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from aota_forge.core.ingress import reset_execution_dispatcher
from aota_forge.composition.thin_task_main_host import (
    THIN_TASK_MAIN_EAGER_OPERATIONS,
    THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS,
    compose_thin_task_main_host,
)
from aota_forge.runtime.trusted_runtime_binding import TrustedBindingError, TrustedWorkerBinding
from aota_forge.work_plane.github_tools import (
    ALL_GITHUB_OPERATIONS,
    GITHUB_ISSUE_READ_DESCRIPTOR,
    GitHubAuthorityError,
    TrustedPlanGitHubBinding,
    create_github_authority,
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

PLAN_REF = "owner-test/aota-hermes-tools#54"


class _FakeHostClient:
    def dispatch(self, payload: Any) -> dict[str, Any]:
        return {"adapter_handle": "fake-1", "status": "running", "dispatch_time": "2026-09-14T00:00:00Z"}


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class FakeGhPort:
    """Deterministic in-process stand-in for the trusted gh backend."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.issue = {
            "number": 54,
            "state": "open",
            "title": "AF Portable Plan #54",
            "body": "# Plan\n\nM5_STATUS=in_progress\n",
            "updated_at": "2026-09-14T00:00:00Z",
            "comments": 2,
            "html_url": "https://example.invalid/54",
        }
        self.comments = [
            {"id": 111, "user": {"login": "op"}, "updated_at": "2026-09-14T00:00:00Z", "body": "milestone progress"},
            {"id": 222, "user": {"login": "op"}, "updated_at": "2026-09-14T00:00:01Z", "body": "x" * 9000},
        ]

    def get_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        self.calls.append(f"get_issue:{repo}#{issue_number}")
        if repo != "owner-test/aota-hermes-tools" or issue_number != 54:
            raise AssertionError("port received a foreign target")
        return dict(self.issue)

    def list_comments(self, repo: str, issue_number: int) -> list[dict[str, Any]]:
        self.calls.append(f"list_comments:{repo}#{issue_number}")
        if repo != "owner-test/aota-hermes-tools" or issue_number != 54:
            raise AssertionError("port received a foreign target")
        return [dict(c) for c in self.comments]

    def update_issue(self, repo: str, issue_number: int, *, body: str | None, state: str | None) -> dict[str, Any]:
        self.calls.append(f"update_issue:{repo}#{issue_number}")
        if body is not None:
            self.issue["body"] = body
        if state is not None:
            self.issue["state"] = state
        self.issue["updated_at"] = "2026-09-14T00:05:00Z"
        return dict(self.issue)

    def update_comment(self, repo: str, comment_id: str, body: str) -> dict[str, Any]:
        self.calls.append(f"update_comment:{comment_id}")
        for c in self.comments:
            if str(c["id"]) == comment_id:
                c["body"] = body
                c["updated_at"] = "2026-09-14T00:06:00Z"
                return dict(c)
        raise AssertionError("foreign comment reached the port")


def _make_root(tmp_path: Path, project_id: str = "af54_m5w2") -> Path:
    root = tmp_path / f"wt-{project_id}"
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        PROJECT_MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
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


def _compose(tmp_path: Path, *, plan_ref: str | None = PLAN_REF):
    root = _make_root(tmp_path)
    return compose_thin_task_main_host(
        worktree_root=root,
        project_id="af54_m5w2",
        worktree_id="m5w2-wt",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260914_af54_m5w2_trusted_session",
        host_client=_FakeHostClient(),
        plan_ref=plan_ref,
    )


def _resolve_payload(host, resp: dict[str, Any]) -> dict[str, Any]:
    """Return the effective payload, hydrating by_ref results via the
    existing governed result.hydrate seam (no second hydration framework)."""
    payload = resp.get("payload")
    if payload:
        return payload
    ref = resp.get("output_ref") or {}
    assert resp.get("output_mode") == "by_ref" and ref, resp
    hydrated = host.invoke("result.hydrate", dict(ref))
    assert hydrated.get("is_success") is True, hydrated
    payload = hydrated.get("payload") or {}
    content = payload.get("content")
    if isinstance(content, str) and "comments" not in payload:
        return json.loads(content)
    return payload


def _err_code(resp: dict[str, Any]) -> str:
    return str((resp.get("error") or {}).get("code", ""))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    yield
    reset_execution_dispatcher()


@pytest.fixture()
def port(monkeypatch) -> FakeGhPort:
    fake = FakeGhPort()
    import aota_forge.work_plane.github_tools as gt

    monkeypatch.setattr(gt, "GhCliGovernancePort", lambda **kwargs: fake)
    return fake


# ---------------------------------------------------------------------------


class TestW2PlanGroundingAndAuthority:
    def test_authorities_minted_for_bound_plan(self, tmp_path: Path) -> None:
        host = _compose(tmp_path)
        names = {a.operation.name for a in host.trusted_binding.github_authorities}
        assert names == set(ALL_GITHUB_OPERATIONS)
        assert host.plan_ref == PLAN_REF
        assert host.trusted_binding.plan_binding.repo == "owner-test/aota-hermes-tools"
        assert host.trusted_binding.plan_binding.issue_number == 54

    def test_unbound_launch_has_no_github_authority(self, tmp_path: Path) -> None:
        host = _compose(tmp_path, plan_ref=None)
        assert host.trusted_binding.github_authorities == ()
        assert host.plan_ref == ""
        resp = host.invoke("github.issue.read", {})
        assert resp.get("is_success") is False
        assert _err_code(resp) == "AUTHORITY_DENIED"

    def test_surface_exposure_split(self, tmp_path: Path) -> None:
        host = _compose(tmp_path)
        assert {"github.issue.read", "github.issue.comments.read"} <= set(THIN_TASK_MAIN_EAGER_OPERATIONS)
        assert {"github.issue.update", "github.issue.comment.update"} <= set(THIN_TASK_MAIN_PROGRESSIVE_OPERATIONS)
        visible = set(host.trusted_binding.tool_surface.all_capability_names())
        assert set(ALL_GITHUB_OPERATIONS) <= visible

    def test_worker_role_cannot_carry_github_authority(self, tmp_path: Path) -> None:
        host = _compose(tmp_path)
        trusted = host.trusted_binding.sandbox
        worker_handoff = TaskHandoff(
            work_role="coder",
            task_kind="worker",
            objective="implement",
            bounded_scope="only src",
            validation_expectations=("pytest",),
            semantic_stop_expectations=("stop",),
        )
        plan = TrustedPlanGitHubBinding.from_plan_ref(PLAN_REF)
        with pytest.raises(GitHubAuthorityError):
            create_github_authority(trusted, worker_handoff, GITHUB_ISSUE_READ_DESCRIPTOR, plan)

    def test_role_bootstrap_carries_bound_plan_ref(self, tmp_path: Path, port) -> None:
        host = _compose(tmp_path)
        guidance = host.role_guidance()
        ctx = guidance["CURRENT_EXECUTION_CONTEXT"]
        assert ctx["plan_ref"] == PLAN_REF


class TestW2ReadOperations:
    def test_bound_issue_read_card(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        resp = host.invoke("github.issue.read", {})
        assert resp.get("is_success") is True, resp
        payload = resp.get("payload") or {}
        assert payload["plan_ref"] == PLAN_REF
        assert "M5_STATUS=in_progress" in payload["body"]
        assert payload["body_digest"] == _digest(port.issue["body"])
        # only the bound target was ever touched
        assert all(c.endswith("owner-test/aota-hermes-tools#54") for c in port.calls if c.startswith("get_issue"))

    def test_model_cannot_redirect_repo_target(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        resp = host.invoke("github.issue.read", {"repo": "evil/repo", "issue": 1})
        assert resp.get("is_success") is False
        assert _err_code(resp) == "UNKNOWN_INPUT"

    def test_generic_github_api_unavailable(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        for bad in ("github.api", "github.exec", "github.request", "gh.run"):
            resp = host.invoke(bad, {})
            assert resp.get("is_success") is False
            assert _err_code(resp) == "UNKNOWN_OPERATION", bad

    def test_comments_read_returns_governance_facts(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        resp = host.invoke("github.issue.comments.read", {})
        assert resp.get("is_success") is True, resp
        comments = _resolve_payload(host, resp)["comments"]
        assert {c["comment_id"] for c in comments} == {"111", "222"}
        assert all(c["author"] == "op" and c["updated_at"] for c in comments)
        big = next(c for c in comments if c["comment_id"] == "222")
        assert big["body_truncated"] is True
        assert big["body_digest"] == _digest("x" * 9000)
        assert comments  # semantic naming is NOT assigned by the Control Plane
        assert "role" not in comments[0]


class TestW2MutationOperations:
    def test_issue_body_update_with_cas(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        read = host.invoke("github.issue.read", {})
        updated_at = (read.get("payload") or {})["updated_at"]
        stale = host.invoke("github.issue.update", {"body": "# Plan v2\n", "expected_updated_at": "old"})
        assert stale.get("is_success") is False
        assert _err_code(stale) == "GITHUB_CAS_CONFLICT"
        ok = host.invoke("github.issue.update", {"body": "# Plan v2\n", "expected_updated_at": updated_at})
        assert ok.get("is_success") is True, ok
        assert port.issue["body"] == "# Plan v2\n"
        # idempotent re-apply: already at desired content (against fresh revision)
        fresh = ok["payload"]["updated_at"]
        again = host.invoke("github.issue.update", {"body": "# Plan v2\n", "expected_updated_at": fresh})
        assert again.get("is_success") is True
        assert (again.get("payload") or {}).get("already_applied") is True

    def test_issue_state_update_for_plan_close(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        read = host.invoke("github.issue.read", {})
        updated_at = (read.get("payload") or {})["updated_at"]
        ok = host.invoke("github.issue.update", {"state": "closed", "expected_updated_at": updated_at})
        assert ok.get("is_success") is True, ok
        assert port.issue["state"] == "closed"

    def test_section_upsert_is_mechanical_and_idempotent(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        read = host.invoke("github.issue.read", {})
        updated_at = (read.get("payload") or {})["updated_at"]
        first = host.invoke(
            "github.issue.update",
            {
                "section_marker": "af54-m5-status",
                "section_content": "M5_STATUS=completed",
                "expected_updated_at": updated_at,
            },
        )
        assert first.get("is_success") is True, first
        assert "# Plan" in port.issue["body"]  # existing content preserved
        assert "af54-m5-status:begin" in port.issue["body"]
        second = host.invoke(
            "github.issue.update",
            {
                "section_marker": "af54-m5-status",
                "section_content": "M5_STATUS=completed",
                "expected_updated_at": first["payload"]["updated_at"],
            },
        )
        assert second.get("is_success") is True
        assert (second.get("payload") or {}).get("already_applied") is True

    def test_comment_update_bound_membership_and_digest_cas(self, tmp_path: Path, port: FakeGhPort) -> None:
        host = _compose(tmp_path)
        read = host.invoke("github.issue.comments.read", {})
        comments = _resolve_payload(host, read)["comments"]
        c111 = next(c for c in comments if c["comment_id"] == "111")
        foreign = host.invoke(
            "github.issue.comment.update",
            {"comment_id": "999999", "body": "not ours", "expected_digest": c111["body_digest"]},
        )
        assert foreign.get("is_success") is False
        assert _err_code(foreign) == "FOREIGN_COMMENT_DENIED"
        stale = host.invoke(
            "github.issue.comment.update",
            {"comment_id": "111", "body": "milestone progress v2", "expected_digest": "0" * 64},
        )
        assert stale.get("is_success") is False
        assert _err_code(stale) == "GITHUB_CAS_CONFLICT"
        ok = host.invoke(
            "github.issue.comment.update",
            {"comment_id": "111", "body": "milestone progress v2", "expected_digest": c111["body_digest"]},
        )
        assert ok.get("is_success") is True, ok
        assert port.comments[0]["body"] == "milestone progress v2"

    def test_binding_rejects_github_authority_on_worker_role(self, tmp_path: Path) -> None:
        host = _compose(tmp_path)
        worker_handoff = TaskHandoff(
            work_role="coder",
            task_kind="worker",
            objective="x",
            bounded_scope="only src",
            validation_expectations=("v",),
            semantic_stop_expectations=("s",),
        )
        from aota_forge.work_plane.af_roles import get_tool_surface_for_role

        with pytest.raises(TrustedBindingError):
            TrustedWorkerBinding(
                canonical_task_id="af54_m5w2:t",
                project_id="af54_m5w2",
                worktree_id="m5w2-wt",
                trusted_context=host.trusted_binding.trusted_context,
                handoff=worker_handoff,
                sandbox=host.trusted_binding.sandbox,
                tool_surface=get_tool_surface_for_role("coder"),
                read_authorities=(),
                github_authorities=host.trusted_binding.github_authorities,
                plan_binding=host.trusted_binding.plan_binding,
            )

    def test_malformed_plan_ref_fails_closed(self, tmp_path: Path) -> None:
        for bad in ("no-issue", "owner/repo", "owner/repo#0", "owner/repo#abc", "owner/repo#54/x"):
            with pytest.raises(Exception):
                TrustedPlanGitHubBinding.from_plan_ref(bad)
