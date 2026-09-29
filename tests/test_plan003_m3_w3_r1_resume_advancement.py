"""Plan #003 M3/W3-R1 resume advancement and projection CAS proofs."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.governance.intake import GovernanceIntakeContext, GovernanceIntakeService
from aota_forge.runtime.task_main.context_binding import (
    ContextBindingError,
    DurableTaskMainContextBinding,
    FileBackedTaskMainContextBindingProjection,
)

from test_plan003_m2_w2_same_session_bind import PLAN_BODY, _adapter, _world
from test_plan003_m3_w2_r1_context_resume import (
    PLAN_ID,
    SESSION,
    _bind,
    _inspect,
    _projection_path,
)


def _durable(
    *,
    plan_id: str = PLAN_ID,
    source_kind: str = PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    authority_ref: str = "local-governance:project-a/plan_alpha",
    project_id: str = "project-a",
    project_fingerprint: str = "a" * 64,
    session: str = "ses_resume_contract_exact",
    source_revision: str = "1" * 64,
    source_digest: str = "1" * 64,
    roots_digest: str = "r" * 64,
) -> DurableTaskMainContextBinding:
    return DurableTaskMainContextBinding(
        project_id=project_id,
        project_fingerprint=project_fingerprint,
        plan_binding=PlanAuthorityBinding(
            plan_id=plan_id,
            source_kind=source_kind,
            authority_ref=authority_ref,
            source_revision=source_revision,
            source_digest=source_digest,
        ),
        origin_task_main_session_ref=session,
        authorized_roots_digest=hashlib.sha256(roots_digest.encode()).hexdigest(),
    )


def test_resume_identity_accepts_observation_advance_but_strict_check_rejects_it() -> None:
    historical = _durable()
    current = _durable(source_revision="2" * 64, source_digest="2" * 64)

    with pytest.raises(ContextBindingError) as strict:
        historical.assert_compatible(current)
    assert strict.value.code == "STALE_CONTEXT"
    historical.assert_resume_identity_compatible(current)


def test_projection_refresh_replaces_only_current_plan_observation(tmp_path: Path) -> None:
    projection_path = tmp_path / ".aota" / "task-main-context-bindings.json"
    projection_path.parent.mkdir()
    projection = FileBackedTaskMainContextBindingProjection(projection_path)
    historical = _durable()
    current = _durable(source_revision="2" * 64, source_digest="2" * 64)
    projection.persist(historical)

    refreshed = projection.refresh_for_session(
        expected_binding_digest=historical.binding_digest,
        replacement=current,
    )

    assert refreshed.binding_digest == current.binding_digest
    assert projection.get_for_session(historical.origin_task_main_session_ref) == current


def test_projection_refresh_detects_cas_race_and_preserves_newer_record(tmp_path: Path) -> None:
    projection_path = tmp_path / ".aota" / "task-main-context-bindings.json"
    projection_path.parent.mkdir()
    projection = FileBackedTaskMainContextBindingProjection(projection_path)
    historical = _durable()
    newer = _durable(source_revision="2" * 64, source_digest="2" * 64)
    projection.persist(historical)
    projection._persist([newer])

    with pytest.raises(ContextBindingError) as conflict:
        projection.refresh_for_session(
            expected_binding_digest=historical.binding_digest,
            replacement=_durable(source_revision="3" * 64, source_digest="3" * 64),
        )

    assert conflict.value.code == "CONTEXT_BINDING_CONFLICT"
    assert projection.get_for_session(historical.origin_task_main_session_ref) == newer


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("plan_id", "PLAN_CONTEXT_MISMATCH"),
        ("authority_ref", "PLAN_CONTEXT_MISMATCH"),
        ("source_kind", "PLAN_CONTEXT_MISMATCH"),
        ("project_fingerprint", "PROJECT_CONTEXT_MISMATCH"),
        ("session", "SESSION_RECOVERY_REQUIRED"),
    ),
)
def test_resume_identity_drift_is_denied(field: str, expected_code: str) -> None:
    historical = _durable()
    values = {
        "plan_id": "plan_beta",
        "authority_ref": "local-governance:project-a/foreign-authority",
        "source_kind": "github_issue",
        "project_fingerprint": "b" * 64,
        "session": "ses_resume_contract_other",
    }
    current = _durable(**{field: values[field]})

    with pytest.raises(ContextBindingError) as denied:
        historical.assert_resume_identity_compatible(current)
    assert denied.value.code == expected_code


def test_projection_refresh_rejects_root_drift(tmp_path: Path) -> None:
    projection_path = tmp_path / ".aota" / "task-main-context-bindings.json"
    projection_path.parent.mkdir()
    projection = FileBackedTaskMainContextBindingProjection(projection_path)
    historical = _durable()
    projection.persist(historical)
    replacement = _durable(
        source_revision="2" * 64,
        source_digest="2" * 64,
        roots_digest="different-roots",
    )

    with pytest.raises(ContextBindingError) as denied:
        projection.refresh_for_session(
            expected_binding_digest=historical.binding_digest,
            replacement=replacement,
        )
    assert denied.value.code == "AUTHORITY_DENIED"
    assert projection.get_for_session(historical.origin_task_main_session_ref) == historical


def test_canonical_plan_advancement_survives_adapter_recreation_and_refreshes_projection(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path)
    _bind(world)
    projection = FileBackedTaskMainContextBindingProjection(_projection_path(world))
    historical = projection.get_for_session(SESSION)
    assert historical is not None

    context = GovernanceIntakeContext(
        workspace_id="trusted-workspace",
        governance_base=world["governance_base"],
        registry_path=world["registry"],
        workspace_root=world["workspace"],
        project_root=world["project_root"],
        governance_store_path=world["store"],
        journal_path=Path(world["project_root"]) / ".aota" / "governance-journal.json",
    )
    advanced_body = PLAN_BODY + "CANONICAL_ADVANCEMENT=R2\n"
    amendment = GovernanceIntakeService(context, principal="m3-w3-r1-test").plan(
        "amend",
        project_id="project-a",
        plan_id=PLAN_ID,
        candidate_body=advanced_body,
        semantic_decision_ref="m3-w3-r1-canonical-advancement",
    )
    assert amendment["status"] == "VERIFIED", amendment

    resumed = _adapter(world, origin=SESSION)
    readback = _inspect(resumed)
    assert readback["ok"] is True, readback
    assert readback["payload"]["state"] == "bound"
    assert readback["payload"]["context"]["plan_id"] == PLAN_ID

    updated = projection.get_for_session(SESSION)
    assert updated is not None
    assert updated.plan_binding.source_digest != historical.plan_binding.source_digest
    assert updated.plan_binding.source_revision != historical.plan_binding.source_revision
    assert (
        updated.plan_binding.source_digest
        == updated.plan_binding.source_revision
    )


def test_raw_plan_file_drift_denies_resume_without_projection_refresh(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    projection = FileBackedTaskMainContextBindingProjection(_projection_path(world))
    historical = projection.get_for_session(SESSION)
    assert historical is not None
    plan_path = Path(world["governance_base"]) / "project-a" / PLAN_ID / "plan.md"
    plan_path.write_text(plan_path.read_text(encoding="utf-8") + "RAW_DRIFT_ONLY=yes\n", encoding="utf-8")

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)

    assert result["ok"] is False
    assert resumed._bound_delegate is None
    assert result["error"]["code"] in {
        "PLAN_AUTHORITY_SOURCE_UNAVAILABLE",
        "STALE_CONTEXT",
        "AUTHORITY_RESOLUTION_FAILED",
    }
    current = projection.get_for_session(SESSION)
    assert current is not None
    assert current.binding_digest == historical.binding_digest


def test_trusted_root_drift_denies_resume_without_projection_refresh(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _bind(world)
    projection = FileBackedTaskMainContextBindingProjection(_projection_path(world))
    historical = projection.get_for_session(SESSION)
    assert historical is not None

    replacement_governance = tmp_path / "replacement-plans"
    shutil.copytree(world["governance_base"], replacement_governance)
    world["governance_base"] = replacement_governance

    resumed = _adapter(world, origin=SESSION)
    result = _inspect(resumed)

    assert result["ok"] is False
    assert result["error"]["code"] == "AUTHORITY_DENIED"
    assert resumed._bound_delegate is None
    current = projection.get_for_session(SESSION)
    assert current is not None
    assert current.binding_digest == historical.binding_digest
