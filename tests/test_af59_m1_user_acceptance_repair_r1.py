"""AF #59 M1 user-acceptance repair R1 — focused deterministic validation.

Task: AF_59_M1_USER_ACCEPTANCE_REPAIR_R1 (bounded acceptance repair).

Covers the task-main capabilities exposed as missing by real :3002 user
acceptance, without widening anything unrelated:

* R2: an ordinary task-main session exposes the canonical read-only
  introspection operations ``operations.list`` / ``host.status`` /
  ``runtime.status`` with no Plan/session binding.
* R3: unbound ``role.bootstrap`` exposes the trusted task-main Skill
  identity/ref metadata from the one AF role/Skill catalog, and
  ``skill.open(ref=...)`` works unbound (read-only guidance, no approval).
* R4: ``git.status`` / ``git.diff`` are ``plan_ref``-scoped and usable while
  the current Milestone approval is ``no``.
* R5: ``git.checkpoint`` / ``git.integrate`` / ``git.push`` are
  ``plan_ref``-scoped governed mutations: unapproved Plans are denied
  ``MILESTONE_APPROVAL_REQUIRED``; an approved fixture reaches the existing
  governed provider path (expected-head CAS, FF-only integration, configured
  branch/remote, never force).
* R6: no unrelated capability widening (restricted_shell.run / test.run /
  workspace.write and legacy task_main.* stay off the unbound surface).
* wrong Plan refs fail closed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "af59repair"
SOURCE_REPOSITORY = "wzjcccc-dotcom/af59repair"
PLAN_REF = f"{SOURCE_REPOSITORY}#59"

PLAN_BODY_TEMPLATE = """# [PLAN] AF #59 repair deterministic fixture

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

FIXTURE_MANIFEST = """schema_version: 1
project:
  id: af59repair
  name: AF59 Repair Test
  kind: forge-core
  status: active
summary: AF 59 repair deterministic fixture project.
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
        # The fake live Plan read only knows this exact ref; a wrong/unknown
        # Plan ref fails closed (never resolves another project's root).
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
    """Composed unbound bindings bind the canonical dispatcher globally."""
    yield
    from aota_forge.core.ingress import reset_execution_dispatcher

    reset_execution_dispatcher()


@pytest.fixture()
def fixture_project(tmp_path: Path):
    """Deterministic trusted project + registry + offline bare remote.

    The project worktree stays on its own branch (``aota/m1/work``) while the
    integration branch ``main`` sits at the accepted base, so the existing
    FF-only integration semantics are exercised exactly. ``origin`` keeps the
    GitHub identity required by trusted project resolution; the local bare
    ``testorigin`` remote is the configured (offline) push target.
    """
    root = tmp_path / "project"
    root.mkdir()
    (root / ".aota").mkdir()
    (root / ".aota" / "project.yaml").write_text(FIXTURE_MANIFEST, encoding="utf-8")
    (root / "README.md").write_text("# af59repair fixture\n", encoding="utf-8")
    (root / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "af59repair@test.local")
    _git(root, "config", "user.name", "af59repair")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "remote", "add", "origin", f"https://github.com/{SOURCE_REPOSITORY}.git")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True, timeout=30)
    _git(root, "remote", "add", "testorigin", str(origin))
    _git(root, "push", "-q", "testorigin", "main")
    _git(root, "checkout", "-q", "-b", "aota/m1/work")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"ws-af59repair": {"candidates": [str(root)]}}), encoding="utf-8"
    )
    return root, registry, origin


def _adapter(
    root: Path,
    registry: Path,
    approval: str,
    *,
    git_branch: str | None = None,
    git_remote: str | None = None,
):
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
    # Trusted operator Git lifecycle configuration (same channel the deployed
    # host uses through AOTA_GIT_INTEGRATION_BRANCH/AOTA_GIT_REMOTE).
    resolver._git_integration_branch = (
        git_branch if git_branch is not None else ""
    )
    resolver._git_remote = git_remote if git_remote is not None else ""
    return adapter


# ---------------------------------------------------------------------------
# R2/R6/R7 — honest unbound surface
# ---------------------------------------------------------------------------


def test_unbound_surface_is_exactly_the_repair_surface():
    from aota_forge.mcp_transport import _unbound_agent_visible_operations

    ops = set(_unbound_agent_visible_operations())
    required = {
        "github.issue.read",
        "github.issue.comments.read",
        "github.issue.update",
        "github.issue.comment.update",
        "workspace.search",
        "workspace.read",
        "handoff.write",
        "handoff.open",
        "task.start",
        "git.status",
        "git.diff",
        "git.checkpoint",
        "git.integrate",
        "git.push",
        "operations.list",
        "host.status",
        "runtime.status",
        "role.bootstrap",
        "skill.open",
        "result.hydrate",
    }
    assert required <= ops
    # R6: no unrelated widening.
    forbidden = {
        "restricted_shell.run",
        "test.run",
        "workspace.write",
        "task.return",
        "task_main.activate_milestone",
        "task_main.recover_coordinator",
        "task_main.advance_once",
        "task_main.submit_work_projection",
    }
    assert not (forbidden & ops)


def test_unrelated_capabilities_still_fail_closed_unbound(fixture_project):
    root, registry, _origin = fixture_project
    adapter = _adapter(root, registry, "yes")
    for operation, arguments in (
        ("restricted_shell.run", {"command_id": "pytest", "args": ["--version"]}),
        ("test.run", {"runner": "pytest", "targets": []}),
        (
            "workspace.write",
            {"path": "x.txt", "content": "x", "mode": "create_or_replace"},
        ),
    ):
        denied = adapter.invoke(operation, arguments)
        assert denied["ok"] is False, operation
        assert denied["error"]["code"] == "AUTHORITY_DENIED", operation


def test_operations_list_host_status_runtime_status_unbound(fixture_project):
    root, registry, _origin = fixture_project
    adapter = _adapter(root, registry, "no")

    listed = adapter.invoke("operations.list", {})
    assert listed["ok"] is True
    names = [entry["operation"] for entry in listed["payload"]["data"]["operations"]]
    assert {"operations.list", "host.status", "runtime.status"} <= set(names)

    host = adapter.invoke("host.status", {})
    assert host["ok"] is True
    assert isinstance(host["payload"]["data"].get("identity"), dict)

    runtime = adapter.invoke("runtime.status", {"pid": os.getpid()})
    assert runtime["ok"] is True
    assert runtime["payload"]["data"]["running"] is True


# ---------------------------------------------------------------------------
# R3 — Skill progressive access restored (no built-in OpenCode Skill)
# ---------------------------------------------------------------------------


def test_role_bootstrap_exposes_task_main_skill_refs_and_skill_open_unbound(
    fixture_project,
):
    root, registry, _origin = fixture_project
    adapter = _adapter(root, registry, "no")

    boot = adapter.invoke("role.bootstrap", {})
    assert boot["ok"] is True
    payload = boot["payload"]
    base = payload["BASE_SKILLS"]
    progressive = payload["PROGRESSIVE_SKILLS"]
    assert [entry["skill_id"] for entry in base] == ["aota-task-main-control"]
    assert {entry["skill_id"] for entry in progressive} == {
        "aota-workspace-operations",
        "aota-result-hydration",
        "aota-task-main-governance",
    }
    assert payload["SKILL_ACCESS"]["argument"] == "ref"
    assert payload["SKILL_ACCESS"]["IS_AUTHORITY"] is False

    # Every exposed ref must be genuinely openable through skill.open(ref=...).
    for entry in [*base, *progressive]:
        assert entry["ref"] == f"{entry['skill_id']}@1.0.0"
        opened = adapter.invoke("skill.open", {"ref": entry["ref"]})
        assert opened["ok"] is True, (entry["ref"], opened.get("error"))
        assert opened["payload"]["skill_id"] == entry["skill_id"]
        assert opened["payload"]["content"].strip()

    # Canonical input is ``ref`` (never ``skill_ref``); foreign/unknown refs
    # fail closed.
    wrong_argument = adapter.invoke(
        "skill.open", {"skill_ref": "aota-task-main-control@1.0.0"}
    )
    assert wrong_argument["ok"] is False
    assert wrong_argument["error"]["code"] == "INVALID_INPUT"
    unknown = adapter.invoke("skill.open", {"ref": "aota-unknown-skill@1.0.0"})
    assert unknown["ok"] is False
    assert unknown["error"]["code"] in ("AUTHORITY_DENIED", "SKILL_NOT_FOUND")


def test_opencode_builtin_skill_stays_disabled():
    """R3: restore AF Skill access, never the OpenCode built-in Skill."""
    import aota_forge.mcp_transport as mt

    assert mt.MCP_AVAILABILITY_IS_NOT_AUTHORITY is True
    assert mt.EXPOSURE_IS_NOT_AUTHORITY is True
    config = (
        Path("/home/latios/.local/share/aota-forge/opencode-reference/runtime/xdg-config/opencode/opencode.json")
    )
    if config.is_file():
        document = json.loads(config.read_text(encoding="utf-8"))
        assert document.get("tools", {}).get("skill") is False


# ---------------------------------------------------------------------------
# R4 — ref-scoped Git reads usable while approval is no
# ---------------------------------------------------------------------------


def test_unapproved_plan_reads_and_git_reads_usable(fixture_project):
    root, registry, _origin = fixture_project
    adapter = _adapter(root, registry, "no")

    read = adapter.invoke(
        "workspace.read",
        {"plan_ref": PLAN_REF, "path": "README.md", "max_bytes": 200},
    )
    assert read["ok"] is True
    search = adapter.invoke("workspace.search", {"plan_ref": PLAN_REF, "query": "fixture"})
    assert search["ok"] is True

    status = adapter.invoke("git.status", {"plan_ref": PLAN_REF})
    assert status["ok"] is True, status.get("error")
    assert status["payload"]["project_id"] == PROJECT_ID
    assert status["payload"]["branch"] == "aota/m1/work"

    diff = adapter.invoke("git.diff", {"plan_ref": PLAN_REF})
    assert diff["ok"] is True, diff.get("error")
    assert diff["payload"]["available"] is True


def test_wrong_plan_ref_fails_closed_for_git_reads(fixture_project):
    root, registry, _origin = fixture_project
    adapter = _adapter(root, registry, "yes")
    denied = adapter.invoke(
        "git.status", {"plan_ref": "wzjcccc-dotcom/unknown-project#1"}
    )
    assert denied["ok"] is False
    assert denied["error"]["code"] in (
        "PLAN_UNREADABLE",
        "PROJECT_RESOLUTION_FAILED",
        "PROJECT_ROOT_MISMATCH",
    )


# ---------------------------------------------------------------------------
# R5 — governed Git lifecycle behind the approval gate
# ---------------------------------------------------------------------------


def test_unapproved_plan_lifecycle_mutations_denied(fixture_project):
    root, registry, _origin = fixture_project
    # Configured branch/remote must NOT bypass the approval gate.
    adapter = _adapter(root, registry, "no", git_branch="main", git_remote="testorigin")

    handoff = adapter.invoke(
        "handoff.write",
        {
            "mode": "work_item",
            "plan_ref": PLAN_REF,
            "payload": {
                "work_role": "coder",
                "objective": "x",
                "bounded_scope": "y",
                "work_item_ref": "M1/W1",
                "milestone_ref": "M1",
            },
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

    for operation, arguments in (
        ("git.checkpoint", {"message": "x"}),
        ("git.integrate", {"expected_old_sha": "1" * 40}),
        ("git.push", {}),
    ):
        denied = adapter.invoke(operation, {"plan_ref": PLAN_REF, **arguments})
        assert denied["ok"] is False, operation
        assert denied["error"]["code"] == "MILESTONE_APPROVAL_REQUIRED", operation


def test_approved_fixture_task_start_ref_path_passes(fixture_project, tmp_path, monkeypatch):
    root, registry, _origin = fixture_project
    monkeypatch.setenv("AOTA_HERMES_RUNTIME_ROOT", str(tmp_path / "hermes-runtime"))
    adapter = _adapter(root, registry, "yes")

    handoff = adapter.invoke(
        "handoff.write",
        {
            "mode": "work_item",
            "plan_ref": PLAN_REF,
            "payload": {
                "work_role": "coder",
                "objective": "fixture",
                "bounded_scope": "fixture scope",
                "work_item_ref": "M1/W1",
                "milestone_ref": "M1",
            },
        },
    )
    assert handoff["ok"] is True, handoff.get("error")
    ref = handoff["payload"]["ref"]
    assert ref.startswith("handoff:")

    start = adapter.invoke(
        "task.start", {"plan_ref": PLAN_REF, "role": "coder", "handoff_ref": ref}
    )
    assert start["ok"] is True, start.get("error")
    assert start["payload"]["status"] == "QUEUED"
    assert start["payload"]["handoff_digest"]


def test_approved_fixture_git_lifecycle_reaches_governed_provider(fixture_project):
    root, registry, origin = fixture_project
    adapter = _adapter(root, registry, "yes", git_branch="main", git_remote="testorigin")

    base = _git(root, "rev-parse", "HEAD")
    (root / "tracked.py").write_text("VALUE = 42\n", encoding="utf-8")

    stale = adapter.invoke(
        "git.checkpoint",
        {"plan_ref": PLAN_REF, "message": "stale", "expected_head": "0" * 40},
    )
    assert stale["ok"] is False
    assert stale["error"]["code"] == "GIT_CAS_CONFLICT"

    checkpoint = adapter.invoke(
        "git.checkpoint",
        {"plan_ref": PLAN_REF, "message": "accept repair work", "expected_head": base},
    )
    assert checkpoint["ok"] is True, checkpoint.get("error")
    new_head = checkpoint["payload"]["commit_sha"]
    assert checkpoint["payload"]["parent_sha"] == base
    assert new_head == _git(root, "rev-parse", "HEAD")

    integrate = adapter.invoke(
        "git.integrate", {"plan_ref": PLAN_REF, "expected_old_sha": base}
    )
    assert integrate["ok"] is True, integrate.get("error")
    assert integrate["payload"]["fast_forward_only"] is True
    assert integrate["payload"]["branch"] == "main"
    assert _git(root, "rev-parse", "refs/heads/main") == new_head

    push = adapter.invoke("git.push", {"plan_ref": PLAN_REF})
    assert push["ok"] is True, push.get("error")
    assert push["payload"]["forced"] is False
    assert push["payload"]["remote"] == "testorigin"
    remote = subprocess.run(
        ["git", "ls-remote", str(origin), "refs/heads/main"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.split()
    assert remote and remote[0] == new_head


def test_unconfigured_lifecycle_authority_fails_closed_when_approved(fixture_project):
    """Approved but operator-unconfigured branch/remote => no lifecycle authority."""
    root, registry, _origin = fixture_project
    adapter = _adapter(root, registry, "yes")  # no branch/remote configured
    denied = adapter.invoke("git.checkpoint", {"plan_ref": PLAN_REF, "message": "x"})
    assert denied["ok"] is False
    assert denied["error"]["code"] in ("AUTHORITY_DENIED", "GOVERNED_OPERATION_FAILURE")
