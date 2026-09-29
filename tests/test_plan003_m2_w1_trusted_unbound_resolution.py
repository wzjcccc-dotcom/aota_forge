"""Plan #003 M2/W1 trusted unbound Project/Plan selector resolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.composition.unbound_resolution import (
    CONTROL_PLANE_SEMANTIC_RANKING,
    CWD_IS_AUTHORITY,
    FILESYSTEM_PATH_IS_AUTHORITY,
    LOCAL_GOV2_RESOLUTION_INDEPENDENT_OF_GITHUB,
    MODEL_MINTS_AUTHORITY,
    PLAN_AMBIGUOUS,
    PLAN_NOT_FOUND,
    SELECTOR_IS_LOCATOR_ONLY,
    SAME_SESSION_BIND_MATERIALIZATION_EXECUTED,
    UnboundResolutionError,
    resolve_trusted_unbound_resolution,
)
from aota_forge.composition.plan_authority import PlanAuthorityCompositionError
from aota_forge.core.contracts.errors import ProjectAmbiguousError, ProjectNotFoundError
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    ProjectPlanRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore


MANIFEST = """schema_version: 1
project:
  id: {project_id}
  name: {project_name}
  kind: test
  status: active
summary: bounded selector fixture
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

PLAN_BODY = """```text
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=ready
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abcdef1234567890
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```
# selector fixture Plan
"""


def _write_project(root: Path, project_id: str, project_name: str) -> None:
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id, project_name=project_name),
        encoding="utf-8",
    )


def _write_plan(
    governance_base: Path,
    project_id: str,
    plan_id: str,
    *,
    authority_ref: str | None = None,
) -> PlanAuthorityBinding:
    body = PLAN_BODY
    plan_dir = governance_base / project_id / plan_id
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=authority_ref or local_plan_authority_reference(project_id, plan_id),
        source_revision=digest,
        source_digest=digest,
    )


def _world(tmp_path: Path, *, project_id: str = "project-a", project_name: str = "Project A") -> dict[str, Path | str]:
    workspace = tmp_path / "workspace"
    project_root = workspace / project_id
    _write_project(project_root, project_id, project_name)
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"trusted-workspace": {"candidates": [str(workspace)]}}),
        encoding="utf-8",
    )
    governance_base = tmp_path / "plans"
    governance_base.mkdir()
    store_path = tmp_path / "governance.sqlite3"
    return {
        "workspace": workspace,
        "project_root": project_root,
        "registry": registry,
        "governance_base": governance_base,
        "store": store_path,
        "project_id": project_id,
    }


def _add_plan(world: dict[str, Path | str], plan_id: str, *, authority_ref: str | None = None) -> None:
    binding = _write_plan(
        world["governance_base"],  # type: ignore[arg-type]
        world["project_id"],  # type: ignore[arg-type]
        plan_id,
        authority_ref=authority_ref,
    )
    store = SQLiteProjectGovernanceStore(world["store"])  # type: ignore[arg-type]
    try:
        store.put_plan(
            ProjectPlanRecord(
                project_id=world["project_id"],  # type: ignore[arg-type]
                plan_id=plan_id,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=binding,
            )
        )
    finally:
        store.close()


def _resolve(world: dict[str, Path | str], project_selector: str, plan_selector: str | None = None):
    return resolve_trusted_unbound_resolution(
        project_selector,
        plan_selector,
        registry_path=world["registry"],  # type: ignore[arg-type]
        workspace_id="trusted-workspace",
        governance_base=world["governance_base"],  # type: ignore[arg-type]
        governance_store_path=world["store"],  # type: ignore[arg-type]
    )


def test_exact_project_selector_resolves_one_trusted_project(tmp_path: Path) -> None:
    world = _world(tmp_path)
    result = _resolve(world, "project-a")
    assert result.project_id == "project-a"
    assert result.project.candidate is not None
    assert result.project.candidate.project_root == str(world["project_root"])
    assert result.plan_id is None


def test_project_name_selector_is_bounded_exact_match(tmp_path: Path) -> None:
    world = _world(tmp_path, project_name="A Trusted Project")
    result = _resolve(world, "A Trusted Project")
    assert result.project_id == "project-a"


def test_unknown_project_selector_is_typed_not_found(tmp_path: Path) -> None:
    world = _world(tmp_path)
    with pytest.raises(ProjectNotFoundError) as exc_info:
        _resolve(world, "missing-project")
    assert exc_info.value.code == "PROJECT_NOT_FOUND"


def test_ambiguous_project_selector_is_typed_and_not_ranked(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_project(first / "one", "project-a", "Shared Project")
    _write_project(second / "two", "project-b", "Shared Project")
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "workspace-one": {"candidates": [str(first)]},
                "workspace-two": {"candidates": [str(second)]},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProjectAmbiguousError) as exc_info:
        resolve_trusted_unbound_resolution("Shared Project", registry_path=registry)
    assert exc_info.value.code == "PROJECT_AMBIGUOUS"
    assert "candidates" in (exc_info.value.details or {})
    assert CONTROL_PLANE_SEMANTIC_RANKING is False


def test_project_resolution_does_not_use_cwd_or_raw_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    world = _world(tmp_path)
    other = tmp_path / "other-cwd"
    other.mkdir()
    monkeypatch.chdir(other)
    assert _resolve(world, "project-a").project_id == "project-a"
    with pytest.raises(UnboundResolutionError) as exc_info:
        _resolve(world, str(world["project_root"]))
    assert exc_info.value.code == "PROJECT_SELECTOR_INVALID"
    assert CWD_IS_AUTHORITY is False
    assert FILESYSTEM_PATH_IS_AUTHORITY is False


def test_exact_plan_selector_resolves_trusted_local_gov2_binding(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    result = _resolve(world, "project-a", "plan_alpha")
    assert result.plan_id == "plan_alpha"
    assert result.plan_binding is not None
    assert result.plan_binding.authority_ref == local_plan_authority_reference("project-a", "plan_alpha")
    assert result.plan_binding.source_revision == result.plan_binding.source_digest
    assert result.plan_record is not None
    assert result.plan_record.project_id == "project-a"


def test_missing_plan_selector_does_not_auto_select_only_plan(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    result = _resolve(world, "project-a")
    assert result.project_id == "project-a"
    assert result.plan_id is None


def test_changed_local_plan_source_fails_closed(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    plan_path = world["governance_base"] / "project-a" / "plan_alpha" / "plan.md"  # type: ignore[operator]
    plan_path.write_text(PLAN_BODY + "changed\n", encoding="utf-8")
    with pytest.raises(PlanAuthorityCompositionError) as exc_info:
        _resolve(world, "project-a", "plan_alpha")
    assert exc_info.value.code == "PLAN_AUTHORITY_SOURCE_UNAVAILABLE"


def test_unknown_plan_selector_is_typed_not_found(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    with pytest.raises(UnboundResolutionError) as exc_info:
        _resolve(world, "project-a", "plan_missing")
    assert exc_info.value.code == PLAN_NOT_FOUND


def test_ambiguous_plan_selector_is_typed_and_bounded(tmp_path: Path) -> None:
    world = _world(tmp_path)
    shared_ref = "local-governance:project-a/shared-selector"
    _add_plan(world, "plan_alpha", authority_ref=shared_ref)
    _add_plan(world, "plan_beta", authority_ref=shared_ref)
    with pytest.raises(UnboundResolutionError) as exc_info:
        _resolve(world, "project-a", shared_ref)
    assert exc_info.value.code == PLAN_AMBIGUOUS
    assert {item["plan_id"] for item in exc_info.value.candidates} == {"plan_alpha", "plan_beta"}


def test_plan_selector_cannot_escape_resolved_project(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    other_root = tmp_path / "other-workspace" / "project-b"
    _write_project(other_root, "project-b", "Project B")
    other_plan = _write_plan(world["governance_base"], "project-b", "plan_beta")  # type: ignore[arg-type]
    store = SQLiteProjectGovernanceStore(world["store"])  # type: ignore[arg-type]
    try:
        store.put_plan(
            ProjectPlanRecord(
                project_id="project-b",
                plan_id="plan_beta",
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=other_plan,
            )
        )
    finally:
        store.close()
    with pytest.raises(UnboundResolutionError) as exc_info:
        _resolve(world, "project-a", "plan_beta")
    assert exc_info.value.code == PLAN_NOT_FOUND


def test_model_cannot_supply_plan_authority_binding(tmp_path: Path) -> None:
    world = _world(tmp_path)
    with pytest.raises(TypeError):
        resolve_trusted_unbound_resolution(
            "project-a",
            registry_path=world["registry"],  # type: ignore[arg-type]
            workspace_id="trusted-workspace",
            plan_binding=object(),  # type: ignore[call-arg]
        )
    assert MODEL_MINTS_AUTHORITY is False
    assert SELECTOR_IS_LOCATOR_ONLY is True


def test_local_resolution_does_not_depend_on_github_or_bind_session(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _add_plan(world, "plan_alpha")
    result = _resolve(world, "project-a", "plan_alpha")
    assert result.plan_binding is not None
    assert LOCAL_GOV2_RESOLUTION_INDEPENDENT_OF_GITHUB is True
    assert SAME_SESSION_BIND_MATERIALIZATION_EXECUTED is False
