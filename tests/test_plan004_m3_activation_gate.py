from __future__ import annotations

import pytest

from scripts.plan004_m3_activation_gate import (
    ActivationFailureClass,
    ActivationGateError,
    RollbackFailed,
    apply_failure_policy,
    validate_p0_static_provenance,
    validate_p0l_live_child_provenance,
)


def _p0_facts(**extra: object) -> dict[str, object]:
    return {
        "opencode_4096_healthy": True,
        "opencode_4095_healthy": True,
        "bridge_healthy": True,
        "service_config_runtime_matches_candidate": True,
        "mcp_command_runtime_matches_candidate": True,
        "plugin_config_hash_matches": True,
        "bridge_config_hash_matches": True,
        "candidate_runtime_root_exists": True,
        "candidate_runtime_is_immutable_and_not_worktree": True,
        "candidate_manifest_digest_matches": True,
        "candidate_source_provenance_matches": True,
        "project_manifest_present": True,
        "operations_contract_present": True,
        "operation_descriptor_count_is_43": True,
        "configured_mcp_surface_is_single_aota_entry": True,
        **extra,
    }


def _p0l_facts(**extra: object) -> dict[str, object]:
    return {
        "session_hash": "a" * 64,
        "genuine_headed_w1_session": True,
        "directory_instance_matches_w1": True,
        "child_pid": 421,
        "child_ppid": 400,
        "child_parent_matches_opencode": True,
        "child_cwd_matches_directory_instance": True,
        "child_command_matches_candidate_wrapper": True,
        "child_runtime_root_matches_candidate": True,
        "child_source_provenance_matches": True,
        "child_candidate_identity_matches": True,
        "tools_list": ["aota.invoke"],
        **extra,
    }


@pytest.mark.parametrize(
    "failure_class,healthy,provenance,expected_calls,expected_reason",
    [
        (ActivationFailureClass.FUNCTIONAL_FAILURE, True, True, 0, None),
        (ActivationFailureClass.MANUAL_GATE, True, True, 0, None),
        (ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE, True, False, 1, None),
        (ActivationFailureClass.PRODUCTION_UNHEALTHY, False, True, 1, None),
        (ActivationFailureClass.UNKNOWN_ACTIVATION_FAILURE, None, None, 1, "UNKNOWN_SAFETY_STATE"),
    ],
)
def test_rollback_policy_classifies_once(
    failure_class: ActivationFailureClass,
    healthy: bool | None,
    provenance: bool | None,
    expected_calls: int,
    expected_reason: str | None,
) -> None:
    calls = 0

    def rollback() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "PASS"}

    result = apply_failure_policy(
        failure_class,
        production_healthy=healthy,
        provenance_valid=provenance,
        rollback=rollback,
    )
    assert calls == expected_calls
    assert result.rollback_required is (expected_calls == 1)
    assert result.rollback_executed is (expected_calls == 1)
    assert result.rollback_reason == expected_reason
    assert result.restored is (expected_calls == 1)


def test_unknown_failure_keeps_healthy_provenance_without_rollback() -> None:
    result = apply_failure_policy(
        ActivationFailureClass.UNKNOWN_ACTIVATION_FAILURE,
        production_healthy=True,
        provenance_valid=True,
        rollback=lambda: pytest.fail("safe unknown state must not roll back"),
    )
    assert result.rollback_required is False
    assert result.restored is False


def test_failed_rollback_never_claims_restored() -> None:
    def rollback() -> dict[str, str]:
        raise RuntimeError("restore failed")

    with pytest.raises(RollbackFailed) as error:
        apply_failure_policy(
            ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE,
            production_healthy=True,
            provenance_valid=False,
            rollback=rollback,
        )
    assert error.value.restored is False


def test_p0_passes_after_restart_with_no_directory_instance_or_mcp_child() -> None:
    result = validate_p0_static_provenance(
        _p0_facts(directory_instances=0, mcp_child_pid=None, mcp_child_count=0)
    )
    assert result == {
        "status": "PASS",
        "gate": "P0_STATIC_PROVENANCE",
        "directory_instance_required": False,
        "mcp_child_required": False,
    }


def test_p0_still_fails_on_static_provenance_defect() -> None:
    with pytest.raises(ActivationGateError) as error:
        validate_p0_static_provenance(
            _p0_facts(candidate_manifest_digest_matches=False)
        )
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE


def test_p0l_passes_after_first_valid_directory_instance_mcp_use() -> None:
    result = validate_p0l_live_child_provenance(_p0l_facts())
    assert result["status"] == "PASS"
    assert result["tools_list"] == ["aota.invoke"]


@pytest.mark.parametrize(
    "change",
    [
        {"child_runtime_root_matches_candidate": False},
        {"child_parent_matches_opencode": False},
        {"directory_instance_matches_w1": False},
        {"child_candidate_identity_matches": False},
        {"tools_list": ["aota.invoke", "other.tool"]},
    ],
)
def test_p0l_wrong_child_provenance_or_tool_surface_fails_security(change: dict[str, object]) -> None:
    with pytest.raises(ActivationGateError) as error:
        validate_p0l_live_child_provenance(_p0l_facts(**change))
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE
