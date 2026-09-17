"""AF #59 M2 — bootstrap, Skill routing, help & governance safety (focused).

Deterministic, network-free coverage for the M2 work items:

* W1: ordinary unbound ``role.bootstrap`` delivers usable base guidance
  (materialized, never metadata-only), progressive refs with ``use_when``,
  the actual canonical AOTA operation surface and truthful runtime facts;
  bootstrap is normal startup guidance, never a mechanical gate, authority,
  Plan bind or session bind; a safe read works without a bootstrap first.
* W1: the base Skill is the routing table (governance/checkpoint/integrate/
  close -> ``aota-task-main-governance@1.0.0``; exact mechanics -> ``help``);
  legacy ``task_main.*`` workflow operations are absent from the thin
  task-main role tool surface.
* W2: the three views agree — ``role.bootstrap.AOTA_MCP.operations`` ==
  unbound ``operations.list`` == the canonical role-filtered exposure;
  ``help(operation=...)`` reports the current canonical contract derived from
  the one descriptor authority, exact lookup only.
* W3: a whole-body replacement of a recognized Portable Plan Issue is
  pre-validated (canonical normalizer + identity preservation); an invalid
  candidate fails BEFORE any provider mutation; valid replacement and section
  upsert still work.
* Ref-scoped regression: workspace/git/github reads keep working with
  ``plan_ref`` in an unbound session.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "af59m2"
SOURCE_REPOSITORY = "wzjcccc-dotcom/aota-hermes-tools"
PLAN_REF = f"{SOURCE_REPOSITORY}#59"

PLAN_BODY_TEMPLATE = """# [PLAN] AF #59 M2 deterministic fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_KIND=portable_plan
PROJECT_ID={project_id}
SOURCE_REPOSITORY={source_repository}
KNOWN_SOURCE_ROOT={root}
PLAN_STATUS=active
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

FIXTURE_MANIFEST = """schema_version: 1
project:
  id: af59m2
  name: AF59 M2 Test
  kind: forge-core
  status: active
summary: AF 59 M2 deterministic fixture project.
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


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True, timeout=30
    )
    return proc.stdout.strip()


@pytest.fixture(autouse=True)
def _isolate_execution_dispatcher():
    yield
    from aota_forge.core.ingress import reset_execution_dispatcher

    reset_execution_dispatcher()


@pytest.fixture()
def fixture_project(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / ".aota").mkdir()
    (root / ".aota" / "project.yaml").write_text(FIXTURE_MANIFEST, encoding="utf-8")
    (root / "README.md").write_text("# af59m2 fixture\n", encoding="utf-8")
    (root / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "af59m2@test.local")
    _git(root, "config", "user.name", "af59m2")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "remote", "add", "origin", f"https://github.com/{SOURCE_REPOSITORY}.git")
    _git(root, "checkout", "-q", "-b", "aota/m2/work")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"ws-af59m2": {"candidates": [str(root)]}}), encoding="utf-8"
    )
    return root, registry


def _adapter(root: Path, registry: Path, approval: str = "yes"):
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


def _hydrated_payload(adapter, response):
    if response.get("payload") is not None:
        return response["payload"]
    arguments = (response.get("hydration") or {}).get("arguments") or {}
    hydrated = adapter.invoke("result.hydrate", arguments)
    payload = hydrated.get("payload") or {}
    content = payload.get("content")
    return json.loads(content) if isinstance(content, str) else payload


# ---------------------------------------------------------------------------
# W1 — bootstrap is the normal task-main base guidance
# ---------------------------------------------------------------------------


class TestW1BootstrapProjection:
    def test_unbound_bootstrap_delivers_usable_base_guidance(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        boot = adapter.invoke("role.bootstrap", {})
        assert boot["ok"] is True
        payload = boot["payload"]
        assert payload["ROLE"] == "task-main"
        assert payload["ROLE_BOOTSTRAP_REQUIRED_FOR_CHAT"] is False
        assert payload["ROLE_BOOTSTRAP_MECHANICAL_GATE"] is False
        assert payload["IS_AUTHORITY"] is False

        base = payload["BASE_SKILLS"]
        assert [entry["skill_id"] for entry in base] == ["aota-task-main-control"]
        entry = base[0]
        assert entry["ref"] == "aota-task-main-control@1.0.0"
        assert entry["digest"]
        assert entry["is_truncated"] is False
        materialized = entry["materialized"]
        assert isinstance(materialized, str) and len(materialized) > 400
        for marker in (
            "role.bootstrap",
            "plan_ref",
            "handoff.write",
            "task.start",
            "workspace.search",
            "github.issue.read",
            "result.hydrate",
            "help(operation=",
        ):
            assert marker in materialized, marker

        progressive = payload["PROGRESSIVE_SKILLS"]
        refs = {entry["ref"] for entry in progressive}
        assert refs == {
            "aota-workspace-operations@1.0.0",
            "aota-result-hydration@1.0.0",
            "aota-task-main-governance@1.0.0",
        }
        for entry in progressive:
            assert entry["short_description"]
            assert entry["use_when"]

    def test_bootstrap_is_not_authority_plan_or_session_bind(self):
        import aota_forge.work_plane.af_roles as af_roles

        assert af_roles.ROLE_BOOTSTRAP_IS_AUTHORITY is False
        assert af_roles.ROLE_BOOTSTRAP_BINDS_PLAN is False
        assert af_roles.ROLE_BOOTSTRAP_BINDS_SESSION is False
        assert af_roles.ROLE_BOOTSTRAP_MECHANICAL_GATE is False

    def test_safe_read_works_without_bootstrap_first(self, fixture_project):
        """A safe read is not rejected merely because bootstrap was not first."""
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        listed = adapter.invoke("operations.list", {})
        assert listed["ok"] is True
        read = adapter.invoke(
            "workspace.read", {"plan_ref": PLAN_REF, "path": "README.md", "max_bytes": 200}
        )
        assert read["ok"] is True, read.get("error")


# ---------------------------------------------------------------------------
# W1-C/W2-A — base Skill is the routing table
# ---------------------------------------------------------------------------


class TestW1SkillRouting:
    def test_base_guidance_routes_governance_and_help(self):
        import aota_forge.work_plane.af_roles as af_roles

        guidance = af_roles.thin_task_main_eager_guidance()
        assert "aota-task-main-governance@1.0.0" in guidance
        assert "github.issue.read" in guidance
        assert "result.hydrate" in guidance
        assert "help(operation=...)" in guidance
        assert "AUTHORITY_DENIED" in guidance
        base_skill = (
            REPO_ROOT / "skills" / "aota-task-main-control" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "aota-task-main-governance@1.0.0" in base_skill
        assert "help(operation=...)" in base_skill
        assert "workspace.search" in base_skill
        assert "result.hydrate" in base_skill

    def test_no_stale_bound_plan_guidance_in_task_main_skills(self):
        skill_files = [
            "aota-task-main-control",
            "aota-task-main-governance",
            "aota-workspace-operations",
            "aota-result-hydration",
        ]
        stale = (
            "plan_ref grounded at launch",
            "never supply plan_ref",
            "never restate owner/repo/issue",
        )
        for skill_id in skill_files:
            text = (REPO_ROOT / "skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")
            for phrase in stale:
                assert phrase not in text, (skill_id, phrase)

    def test_workspace_skill_supports_task_main_unbound_plan_ref(self):
        text = (
            REPO_ROOT / "skills" / "aota-workspace-operations" / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "plan_ref" in text
        assert "normally NOT required" in text

    def test_result_hydration_stop_guidance_present(self):
        text = (
            REPO_ROOT / "skills" / "aota-result-hydration" / "SKILL.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "CROSS_SCOPE_DENIED",
            "DIGEST_MISMATCH",
            "byte_length mismatch",
            "do **not** search for another `output_ref`",
            "needs_input",
        ):
            assert marker in text, marker

    def test_governance_skill_is_ref_scoped_and_explicit(self):
        text = (
            REPO_ROOT / "skills" / "aota-task-main-governance" / "SKILL.md"
        ).read_text(encoding="utf-8")
        for marker in (
            "locates the current server-side authority",
            "WHOLE BODY REPLACEMENT",
            "section_marker",
            "expected_updated_at",
            "Never probe mutation schemas",
        ):
            assert marker in text, marker


# ---------------------------------------------------------------------------
# W1-D — converged task-main role tool surface
# ---------------------------------------------------------------------------


class TestW1RoleToolSurface:
    def test_legacy_workflow_ops_absent_from_thin_task_main(self):
        from aota_forge.work_plane.af_roles import (
            LEGACY_TASK_MAIN_WORKFLOW_OPS_VISIBLE_TO_THIN_TASK_MAIN,
            get_tool_surface_for_role,
        )

        assert LEGACY_TASK_MAIN_WORKFLOW_OPS_VISIBLE_TO_THIN_TASK_MAIN is False
        surface = get_tool_surface_for_role("task-main")
        names = set(surface.all_capability_names())
        for legacy in (
            "task_main.activate_milestone",
            "task_main.recover_coordinator",
            "task_main.advance_once",
            "task_main.submit_work_projection",
        ):
            assert legacy not in names, legacy

    def test_required_operations_present_on_task_main_surface(self):
        from aota_forge.work_plane.af_roles import get_tool_surface_for_role

        names = set(get_tool_surface_for_role("task-main").all_capability_names())
        for required in (
            "role.bootstrap",
            "skill.open",
            "help",
            "operations.list",
            "github.issue.read",
            "github.issue.comments.read",
            "github.issue.update",
            "github.issue.comment.update",
            "workspace.search",
            "workspace.read",
            "handoff.write",
            "handoff.open",
            "task.start",
            "result.hydrate",
            "git.status",
            "git.diff",
            "git.checkpoint",
            "git.integrate",
            "git.push",
            "host.status",
            "runtime.status",
        ):
            assert required in names, required
        for off_surface in (
            "workspace.write",
            "test.run",
            "restricted_shell.run",
            "task.return",
        ):
            assert off_surface not in names, off_surface

    def test_surface_derives_from_canonical_exposure(self):
        from aota_forge.composition.ref_scoped_authority import (
            canonical_task_main_operations,
        )
        from aota_forge.work_plane.af_roles import get_tool_surface_for_role

        names = set(get_tool_surface_for_role("task-main").all_capability_names())
        assert names == set(canonical_task_main_operations())


# ---------------------------------------------------------------------------
# W2-F — one operation-surface truth; W2-E — help
# ---------------------------------------------------------------------------


class TestW2OperationSurfaceTruth:
    def test_bootstrap_operations_equal_operations_list(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        boot = adapter.invoke("role.bootstrap", {})
        listed = adapter.invoke("operations.list", {})
        assert boot["ok"] is True and listed["ok"] is True
        bootstrap_ops = set(boot["payload"]["AOTA_MCP"]["operations"])
        listed_ops = {entry["operation"] for entry in listed["payload"]["data"]["operations"]}
        assert bootstrap_ops == listed_ops
        assert "help" in bootstrap_ops
        assert "operations.list" in bootstrap_ops
        for legacy in (
            "task_main.activate_milestone",
            "task_main.recover_coordinator",
            "task_main.advance_once",
            "task_main.submit_work_projection",
        ):
            assert legacy not in bootstrap_ops

    def test_help_github_issue_update_reports_current_contract(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        response = adapter.invoke("help", {"operation": "github.issue.update"})
        assert response["ok"] is True
        payload = response["payload"]
        assert payload["operation"] == "github.issue.update"
        assert payload["kind"] == "mutation"
        assert payload["read_or_write"] == "write"
        assert payload["authority_locator"] == "plan_ref"
        assert payload["body_semantics"] == "WHOLE_BODY_REPLACEMENT"
        assert payload["section_marker+section_content"] == "BOUNDED_SECTION_UPSERT"
        assert payload["state"] == "open|closed"
        assert payload["CAS_semantics"] == "expected_updated_at=CAS_PRECONDITION"
        assert payload["preferred_plan_state_update"] == "section_marker+section_content"
        assert payload["relevant_skill_ref"] == "aota-task-main-governance@1.0.0"
        assert "expected_updated_at" in payload["required_inputs"]
        assert {"body", "section_marker", "section_content", "state", "plan_ref"} <= set(
            payload["optional_inputs"]
        )

    def test_help_derives_from_descriptor_for_reads(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        response = adapter.invoke("help", {"operation": "github.issue.read"})
        assert response["ok"] is True
        payload = response["payload"]
        assert payload["kind"] == "read"
        assert payload["authority_locator"] == "plan_ref"
        assert "body_semantics" not in payload
        assert payload["relevant_skill_ref"] == "aota-task-main-governance@1.0.0"

    def test_help_unknown_operation_is_typed_no_fuzzy_lookup(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        response = adapter.invoke("help", {"operation": "github.issue.udpate"})
        assert response["ok"] is False
        assert response["error"]["code"] == "UNKNOWN_OPERATION"

    def test_help_rejects_bad_input(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry)
        missing = adapter.invoke("help", {})
        assert missing["ok"] is False
        assert missing["error"]["code"] == "INVALID_INPUT"
        wrong_type = adapter.invoke("help", {"operation": 7})
        assert wrong_type["ok"] is False
        assert wrong_type["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# W3 — Portable Plan whole-body replacement safety
# ---------------------------------------------------------------------------


class _FakeGovernancePort:
    """Deterministic in-process stand-in for the trusted gh backend."""

    def __init__(self, body: str, updated_at: str = "2026-09-16T00:00:00Z") -> None:
        self.calls: list[str] = []
        self.issue = {
            "number": 59,
            "state": "open",
            "title": "AF Portable Plan #59",
            "body": body,
            "updated_at": updated_at,
            "comments": 0,
            "html_url": "https://example.invalid/59",
        }

    def get_issue(self, repo: str, issue_number: int) -> dict:
        self.calls.append(f"get_issue:{repo}#{issue_number}")
        return dict(self.issue)

    def list_comments(self, repo: str, issue_number: int) -> list[dict]:
        self.calls.append(f"list_comments:{repo}#{issue_number}")
        return []

    def update_issue(self, repo: str, issue_number: int, *, body, state) -> dict:
        self.calls.append(f"update_issue:{repo}#{issue_number}")
        if body is not None:
            self.issue["body"] = body
        if state is not None:
            self.issue["state"] = state
        self.issue["updated_at"] = "2026-09-16T00:05:00Z"
        return dict(self.issue)

    def update_comment(self, repo: str, comment_id: str, body: str) -> dict:
        raise AssertionError("comment update not expected")


def _bound_plan_ref_host(tmp_path: Path, monkeypatch, body: str):
    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
    import aota_forge.work_plane.github_tools as gt

    port = _FakeGovernancePort(body)
    monkeypatch.setattr(gt, "GhCliGovernancePort", lambda **kwargs: port)
    root = tmp_path / "plan-wt"
    (root / ".aota").mkdir(parents=True)
    manifest = FIXTURE_MANIFEST.replace("id: af59m2", "id: af59m2")
    (root / ".aota" / "project.yaml").write_text(manifest, encoding="utf-8")
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
    host = compose_thin_task_main_host(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="m2-wt",
        runtime_config_path=cfg,
        origin_task_main_session_ref="20260916_af59_m2_trusted_session",
        plan_ref=PLAN_REF,
    )
    return host, port


def _plan_fixture_body() -> str:
    return PLAN_BODY_TEMPLATE.format(
        project_id=PROJECT_ID,
        source_repository=SOURCE_REPOSITORY,
        root="/tmp/does-not-matter",
        approval="yes",
    )


class TestW3PlanBodySafety:
    def test_probe_candidate_rejected_before_provider_write(self, tmp_path, monkeypatch):
        current = _plan_fixture_body()
        host, port = _bound_plan_ref_host(tmp_path, monkeypatch, current)
        response = host.invoke(
            "github.issue.update",
            {
                "body": "# test update attempt\n```text\nPLAN_STATUS=completed\n```\n",
                "expected_updated_at": "2026-09-16T00:00:00Z",
            },
        )
        assert response.get("is_success") is False
        assert (response.get("error") or {}).get("code") == "INVALID_INPUT"
        assert "PLAN_BODY_INVALID" in str((response.get("error") or {}).get("message"))
        assert not any(call.startswith("update_issue") for call in port.calls), port.calls
        assert port.issue["body"] == current

    def test_incomplete_plan_candidate_rejected_without_provider_write(
        self, tmp_path, monkeypatch
    ):
        current = _plan_fixture_body()
        host, port = _bound_plan_ref_host(tmp_path, monkeypatch, current)
        response = host.invoke(
            "github.issue.update",
            {
                "body": "# test update attempt\nPLAN_STATUS=completed",
                "expected_updated_at": "2026-09-16T00:00:00Z",
            },
        )
        assert response.get("is_success") is False
        assert "PLAN_BODY_INVALID" in str((response.get("error") or {}).get("message"))
        assert not any(call.startswith("update_issue") for call in port.calls), port.calls
        assert port.issue["body"] == current

    def test_valid_complete_replacement_reaches_provider(self, tmp_path, monkeypatch):
        current = _plan_fixture_body()
        host, port = _bound_plan_ref_host(tmp_path, monkeypatch, current)
        candidate = current.replace("M1_STATUS=in_progress", "M1_STATUS=completed")
        response = host.invoke(
            "github.issue.update",
            {"body": candidate, "expected_updated_at": "2026-09-16T00:00:00Z"},
        )
        assert response.get("is_success") is True, response.get("error")
        assert any(call.startswith("update_issue") for call in port.calls), port.calls
        assert port.issue["body"] == candidate

    def test_section_upsert_remains_valid(self, tmp_path, monkeypatch):
        current = _plan_fixture_body()
        host, port = _bound_plan_ref_host(tmp_path, monkeypatch, current)
        response = host.invoke(
            "github.issue.update",
            {
                "section_marker": "AF59_M2_EVIDENCE",
                "section_content": "bounded governance note",
                "expected_updated_at": "2026-09-16T00:00:00Z",
            },
        )
        assert response.get("is_success") is True, response.get("error")
        assert "AF59_M2_EVIDENCE:begin" in port.issue["body"]

    def test_identity_swap_rejected(self):
        from aota_forge.work_plane.github_tools import _portable_plan_replacement_error

        current = _plan_fixture_body()
        candidate = current.replace("PROJECT_ID=af59m2", "PROJECT_ID=other")
        error = _portable_plan_replacement_error(current, candidate)
        assert error is not None and "identity" in error

    def test_plan_kind_alias_conflict_rejected(self):
        from aota_forge.work_plane.github_tools import _portable_plan_replacement_error

        current = _plan_fixture_body()
        candidate = current.replace("PLAN_KIND=portable_plan", "PLAN_KIND=other_kind")
        error = _portable_plan_replacement_error(current, candidate)
        assert error is not None and "conflict" in error

    def test_plan_kind_alias_conflict_on_current_body_rejected(self):
        from aota_forge.work_plane.github_tools import _portable_plan_replacement_error

        current = _plan_fixture_body().replace(
            "PLAN_KIND=portable_plan", "PLAN_KIND=other_kind"
        )
        error = _portable_plan_replacement_error(current, _plan_fixture_body())
        assert error is not None and "current Plan" in error

    def test_non_plan_target_not_constrained(self):
        from aota_forge.work_plane.github_tools import _portable_plan_replacement_error

        assert _portable_plan_replacement_error("# ordinary issue\n", "anything at all") is None

    def test_large_governance_body_passes_canonical_validation(self):
        """The normative Portable Plan body exceeds the generic 4096 string bound."""
        from aota_forge.core.contracts.validation import validate_inputs
        from aota_forge.core_ingress import resolve_descriptor

        big = "# Plan\n" + ("x" * 5000)
        descriptor = resolve_descriptor("github.issue.update")
        validated = validate_inputs(
            descriptor,
            {"body": big, "expected_updated_at": "2026-09-16T00:00:00Z"},
        )
        assert len(validated["body"]) == len(big)
        # the generic bound still protects every other operation/input
        read_descriptor = resolve_descriptor("github.issue.comments.read")
        with pytest.raises(Exception) as excinfo:
            validate_inputs(read_descriptor, {"max_comments": 1, "plan_ref": "x" * 5000})
        assert getattr(excinfo.value, "code", "") == "INPUT_SIZE_EXCEEDED"


# ---------------------------------------------------------------------------
# Ref-scoped regression (workspace / Git / GitHub reads with plan_ref)
# ---------------------------------------------------------------------------


class TestRefScopedRegression:
    def test_plan_bound_reads_keep_working_unbound(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry, approval="no")
        read = adapter.invoke(
            "workspace.read", {"plan_ref": PLAN_REF, "path": "tracked.py", "max_bytes": 200}
        )
        assert read["ok"] is True, read.get("error")
        search = adapter.invoke("workspace.search", {"plan_ref": PLAN_REF, "query": "VALUE"})
        assert search["ok"] is True, search.get("error")
        status = adapter.invoke("git.status", {"plan_ref": PLAN_REF})
        assert status["ok"] is True, status.get("error")
        assert status["payload"]["project_id"] == PROJECT_ID
        diff = adapter.invoke("git.diff", {"plan_ref": PLAN_REF})
        assert diff["ok"] is True, diff.get("error")

    def test_unapproved_side_effect_still_denied(self, fixture_project):
        root, registry = fixture_project
        adapter = _adapter(root, registry, approval="no")
        denied = adapter.invoke("git.checkpoint", {"plan_ref": PLAN_REF, "message": "x"})
        assert denied["ok"] is False
        assert denied["error"]["code"] == "MILESTONE_APPROVAL_REQUIRED"
