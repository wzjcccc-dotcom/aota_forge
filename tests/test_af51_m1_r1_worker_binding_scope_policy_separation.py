"""AF #51 M1/R1 — I51-B001 Worker binding / AGENTS policy scope separation.

Repair invariant:

    WORK_SEMANTIC_PROJECTION_TEXT_IS_AGENTS_SCOPE=no

The Worker binding never tokenizes Work execution scope text
(WorkSemanticProjection.bounded_scope / TaskHandoff.bounded_scope) into
AgentsPolicyCandidate.scope. The synthetic policy applicability label is
derived mechanically from trusted handoff identity (digest prefix) and the
AGENTS logical-scope grammar stays narrow and fail-closed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.roles import AgentWorkRole

PROJECT_ID = "af51r1"
_LABEL_RE = re.compile(r"^work/[0-9a-f]{12}$")

_UNDERSCORE_PATH_SCOPE = "Create src/calculator/__init__.py and implement calculator package."
_DOUBLE_HYPHEN_FLAG_SCOPE = "Implement CLI with --json output."
_COMBINED_SCOPE = (
    "Create src/pkg/__init__.py and foo_bar.py, support --json output, "
    "keep hyphenated-prose, (parentheses), colon: comma, semantics intact."
)


def _ensure_project(root: Path, project_id: str = PROJECT_ID) -> None:
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        "schema_version: 1\n"
        "project:\n"
        f"  id: {project_id}\n"
        "  name: t\n"
        "  kind: test\n"
        "  status: active\n"
        "summary: test\n"
        "capabilities: []\n"
        "paths:\n"
        "  source_root: .\n"
        "  source: []\n"
        "  docs: []\n"
        "  scripts: []\n"
        "  profiles: []\n"
        "  skills: []\n"
        "  tests: []\n"
        "commands:\n"
        "  validate: []\n"
        "  deploy: []\n"
        "  verify_deploy: []\n"
        "runtime:\n"
        "  deployment_type: manual\n"
        "  requires_human_checkpoint: false\n"
        "codegraph:\n"
        "  enabled: false\n"
        "  index_location: .codegraph\n"
        "plan:\n"
        "  active_plan_id: null\n"
        "constraints: []\n",
        encoding="utf-8",
    )


def _handoff(
    bounded_scope: str,
    *,
    work_item: str = "W1",
    objective: str = "bounded worker objective",
) -> TaskHandoff:
    return TaskHandoff(
        work_role=AgentWorkRole.CODER,
        task_kind="calculator-implementation",
        objective=objective,
        bounded_scope=bounded_scope,
        validation_expectations=("focused tests pass",),
        semantic_stop_expectations=("stop on denial",),
        work_item_ref=SemanticReference(ref=work_item),
        milestone_ref=SemanticReference(ref="M1"),
        project_ref=SemanticReference(ref=PROJECT_ID),
    )


def _build(tmp_path: Path, handoff: TaskHandoff):
    _ensure_project(tmp_path)
    binding = build_worker_binding(
        root=tmp_path,
        project_id=PROJECT_ID,
        worktree_id="af51-r1-worktree",
        canonical_task_id="af51r1:M1:W1:attempt-1",
        handoff=handoff,
    )
    policy = binding.read_authorities[0].applicable_policies[0]
    return binding, policy


def _assert_policy_content_exact(policy: AgentsPolicyCandidate, raw_scope: str) -> None:
    assert policy.content == f"Bounded scope derived from TaskHandoff: {raw_scope}"


def _assert_label_not_work_text(policy: AgentsPolicyCandidate, raw_scope: str) -> None:
    assert _LABEL_RE.match(policy.scope), policy.scope
    assert policy.scope != raw_scope
    for token in re.findall(r"[A-Za-z0-9._-]+", raw_scope):
        assert token not in policy.scope


# ---------------------------------------------------------------------------
# Case A — Python package path
# ---------------------------------------------------------------------------


def test_case_a_underscore_package_path_binds_and_is_not_agents_scope(tmp_path: Path) -> None:
    handoff = _handoff(_UNDERSCORE_PATH_SCOPE)
    binding, policy = _build(tmp_path, handoff)
    assert binding.handoff.bounded_scope == _UNDERSCORE_PATH_SCOPE
    _assert_policy_content_exact(policy, _UNDERSCORE_PATH_SCOPE)
    _assert_label_not_work_text(policy, _UNDERSCORE_PATH_SCOPE)
    assert "__init__.py" not in policy.scope


# ---------------------------------------------------------------------------
# Case B — CLI flag
# ---------------------------------------------------------------------------


def test_case_b_double_hyphen_flag_binds_and_is_not_agents_scope(tmp_path: Path) -> None:
    handoff = _handoff(_DOUBLE_HYPHEN_FLAG_SCOPE)
    binding, policy = _build(tmp_path, handoff)
    assert binding.handoff.bounded_scope == _DOUBLE_HYPHEN_FLAG_SCOPE
    _assert_policy_content_exact(policy, _DOUBLE_HYPHEN_FLAG_SCOPE)
    _assert_label_not_work_text(policy, _DOUBLE_HYPHEN_FLAG_SCOPE)
    assert "--json" not in policy.scope


# ---------------------------------------------------------------------------
# Case C — combined legitimate engineering text
# ---------------------------------------------------------------------------


def test_case_c_combined_engineering_text_preserved_exactly(tmp_path: Path) -> None:
    handoff = _handoff(_COMBINED_SCOPE)
    binding, policy = _build(tmp_path, handoff)
    assert binding.handoff.bounded_scope == _COMBINED_SCOPE
    assert binding.handoff.to_dict()["bounded_scope"] == _COMBINED_SCOPE
    _assert_policy_content_exact(policy, _COMBINED_SCOPE)
    _assert_label_not_work_text(policy, _COMBINED_SCOPE)
    for marker in ("src/pkg/__init__.py", "--json", "foo_bar.py", "hyphenated-prose"):
        assert marker in binding.handoff.bounded_scope
        assert marker in policy.content
        assert marker not in policy.scope


# ---------------------------------------------------------------------------
# Case D — AGENTS grammar still narrow (fail-closed, unchanged)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_scope",
    ["__init__.py", "--json", "src/__init__.py", "src/-flags", "src/pkg.name/"],
)
def test_case_d_direct_invalid_agents_scope_still_rejected(invalid_scope: str) -> None:
    with pytest.raises(ValueError):
        AgentsPolicyCandidate(
            policy_id="policy-direct-invalid",
            project_id=PROJECT_ID,
            scope=invalid_scope,
            content="bounded material",
        )


def test_case_d_valid_agents_scope_grammar_unchanged() -> None:
    for valid_scope in ("", "root", "src", "src/pkg", "src/pkg.name"):
        candidate = AgentsPolicyCandidate(
            policy_id="policy-direct-valid",
            project_id=PROJECT_ID,
            scope=valid_scope,
            content="bounded material",
        )
        assert candidate.scope in ("", "src", "src/pkg", "src/pkg.name")


# ---------------------------------------------------------------------------
# Case E — deterministic binding identity
# ---------------------------------------------------------------------------


def test_case_e_same_trusted_identity_same_derived_label(tmp_path: Path) -> None:
    handoff = _handoff(_UNDERSCORE_PATH_SCOPE)
    _, first = _build(tmp_path, handoff)
    _, second = _build(tmp_path, handoff)
    assert first.scope == second.scope
    assert first.policy_id == second.policy_id
    assert first.content_digest == second.content_digest


def test_case_e_different_trusted_identity_different_label(tmp_path: Path) -> None:
    baseline = _handoff(_UNDERSCORE_PATH_SCOPE, work_item="W1")
    _, baseline_policy = _build(tmp_path, baseline)
    other_work_item = _handoff(_UNDERSCORE_PATH_SCOPE, work_item="W2")
    _, other_work_item_policy = _build(tmp_path, other_work_item)
    other_scope_text = _handoff(_UNDERSCORE_PATH_SCOPE + " Extra clause.", work_item="W1")
    _, other_scope_policy = _build(tmp_path, other_scope_text)
    assert other_work_item_policy.scope != baseline_policy.scope
    assert other_scope_policy.scope != baseline_policy.scope
    assert _LABEL_RE.match(baseline_policy.scope)
    assert _LABEL_RE.match(other_work_item_policy.scope)
    assert _LABEL_RE.match(other_scope_policy.scope)
