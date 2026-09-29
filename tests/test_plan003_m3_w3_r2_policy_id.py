"""Plan #003 M3/W3-R2 synthetic Worker policy identity contract."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.work_plane.agents_applicability import (
    AGENTS_POLICY_IS_PLAN_AUTHORITY,
    MAX_POLICY_ID_LENGTH,
    _ID_RE,
)
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff


PROJECT_ID = "aota_forge"
ROLES = ("analyst", "coder", "reviewer", "project-steward")
ALLOWED_PATH_BEARING_WORK_REFS = ("M3/W3", "feature/foo", "nested/work/item")


def _handoff(role: str = "analyst", work_item_ref: str = "M3/W3") -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="plan003-m3-w3-r2",
        objective="bounded worker policy identity validation",
        bounded_scope="read-only source inspection",
        validation_expectations=("focused validation",),
        semantic_stop_expectations=("stop on authority denial",),
        project_ref=SemanticReference(ref=PROJECT_ID),
        milestone_ref=SemanticReference(ref="M3"),
        work_item_ref=SemanticReference(ref=work_item_ref),
    )


def _policy(binding):
    policies = binding.read_authorities[0].applicable_policies
    assert len(policies) == 1
    return policies[0]


def _binding(tmp_path: Path, handoff: TaskHandoff):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return build_worker_binding(
        root=tmp_path,
        project_id=PROJECT_ID,
        worktree_id="wt-r2",
        canonical_task_id="task-r2",
        handoff=handoff,
    )


def test_policy_id_is_opaque_digest_identity_and_provenance_is_separate(tmp_path: Path) -> None:
    handoff = _handoff()
    binding = _binding(tmp_path, handoff)
    policy = _policy(binding)

    expected = f"policy-work-{handoff.compute_handoff_digest()[:12]}"
    assert policy.policy_id == expected
    assert policy.provenance_ref == "M3/M3/W3"
    assert policy.policy_id != policy.provenance_ref
    assert "/" not in policy.policy_id
    assert "\\" not in policy.policy_id
    assert len(policy.policy_id) <= MAX_POLICY_ID_LENGTH
    assert re.fullmatch(_ID_RE, policy.policy_id)
    assert AGENTS_POLICY_IS_PLAN_AUTHORITY is False


def test_same_handoff_is_deterministic_and_distinct_digest_is_distinct(tmp_path: Path) -> None:
    first = _handoff()
    second = _handoff()
    different = _handoff(work_item_ref="feature/foo")

    first_policy = _policy(_binding(tmp_path / "one", first))
    second_policy = _policy(_binding(tmp_path / "two", second))
    different_policy = _policy(_binding(tmp_path / "three", different))

    assert first.compute_handoff_digest() == second.compute_handoff_digest()
    assert first_policy.policy_id == second_policy.policy_id
    assert first.compute_handoff_digest() != different.compute_handoff_digest()
    assert first_policy.policy_id != different_policy.policy_id


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("work_item_ref", ALLOWED_PATH_BEARING_WORK_REFS)
def test_all_worker_roles_keep_path_bearing_work_identity_out_of_policy_id(
    tmp_path: Path, role: str, work_item_ref: str
) -> None:
    handoff = _handoff(role=role, work_item_ref=work_item_ref)
    policy = _policy(_binding(tmp_path, handoff))

    assert handoff.work_item_ref is not None
    assert handoff.work_item_ref.ref == work_item_ref
    assert "/" not in policy.policy_id
    assert "\\" not in policy.policy_id
    assert len(policy.policy_id) <= MAX_POLICY_ID_LENGTH
    assert re.fullmatch(_ID_RE, policy.policy_id)
