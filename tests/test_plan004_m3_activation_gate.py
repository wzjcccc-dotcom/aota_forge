from __future__ import annotations

import pytest

from scripts.plan004_m3_activation_gate import (
    ActivationFailureClass,
    ActivationGateError,
    RollbackFailed,
    apply_failure_policy,
    validate_p0_static_provenance,
    validate_p0l_live_child_provenance,
    validate_v7r1_candidate_immutability,
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
    session_hash = "a" * 64
    call_hash = "b" * 64
    context_hash = "c" * 64
    return {
        "genuine_headed_w1_session": True,
        "original_w1_session_preserved": True,
        "session_id_sha256": session_hash,
        "call_id_sha256": call_hash,
        "session_directory": "/home/latios/workspace",
        "child_cwd": "/home/latios/workspace",
        "child_started_before_session": True,
        "child_pid": 421,
        "child_ppid": 400,
        "child_parent_matches_opencode": True,
        "child_command_matches_candidate_wrapper": True,
        "child_runtime_root_matches_candidate": True,
        "child_source_provenance_matches": True,
        "child_candidate_identity_matches": True,
        "plugin_hook_is_tool_execute_before": True,
        "plugin_session_id_sha256": session_hash,
        "plugin_call_id_sha256": call_hash,
        "bridge_carrier_verified": True,
        "bridge_carrier_session_id_sha256": session_hash,
        "bridge_carrier_call_id_sha256": call_hash,
        "bridge_session_id_sha256": session_hash,
        "bridge_call_id_sha256": call_hash,
        "bridge_session_id_verified_per_invocation": True,
        "bridge_call_id_verified_per_invocation": True,
        "bridge_carrier_session_correlation_verified": True,
        "bridge_carrier_call_id_correlation_verified": True,
        "bridge_one_time_semantics_preserved": True,
        "bridge_carrier_replayed": False,
        "bridge_carrier_substituted": False,
        "child_invocation_session_id_sha256": session_hash,
        "child_invocation_call_id_sha256": call_hash,
        "af_session_id_sha256": session_hash,
        "af_logical_context_session_id_sha256": session_hash,
        "af_logical_context_id_sha256": context_hash,
        "af_expected_logical_context_id_sha256": context_hash,
        "af_context_selected_from_verified_session": True,
        "one_session_one_logical_af_context": True,
        "context_state_before_explicit_bind": "unbound",
        "context_id_before_explicit_bind": None,
        "project_authority_before_explicit_bind": "no",
        "plan_authority_before_explicit_bind": "no",
        "tools_list": ["aota.invoke"],
        **extra,
    }


def _candidate_facts(**extra: object) -> dict[str, object]:
    return {
        "candidate_version": "m3-preparation-v7r1",
        "candidate_manifest_digest": "sha256:47797ea694538d51e73a2e90b4e6b191e4e7a725bbd2f8b42284bf3161be416f",
        "candidate_runtime_source_frontier": "a3a8f8544b892b9d31f847288b1e96eacde635fb",
        "candidate_package_commit": "81d375f49969e49b85b591111eedb94b28386c14",
        "candidate_changed": False,
        "candidate_repackaged": False,
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


def test_candidate_immutability_accepts_only_approved_v7r1_identity() -> None:
    result = validate_v7r1_candidate_immutability(_candidate_facts())
    assert result["status"] == "PASS"
    assert result["candidate_changed"] == "no"
    assert result["candidate_repackaged"] == "no"


@pytest.mark.parametrize(
    "change",
    [
        {"candidate_manifest_digest": "sha256:" + "0" * 64},
        {"candidate_runtime_source_frontier": "0" * 40},
        {"candidate_package_commit": "0" * 40},
        {"candidate_changed": True},
        {"candidate_repackaged": True},
    ],
)
def test_candidate_identity_drift_fails_security(change: dict[str, object]) -> None:
    with pytest.raises(ActivationGateError) as error:
        validate_v7r1_candidate_immutability(_candidate_facts(**change))
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE


@pytest.mark.parametrize("child_started_before_session", [True, False])
def test_preexisting_shared_child_is_allowed_and_creation_time_is_ignored(
    child_started_before_session: bool,
) -> None:
    result = validate_p0l_live_child_provenance(
        _p0l_facts(child_started_before_session=child_started_before_session)
    )
    assert result["status"] == "PASS"
    assert result["tools_list"] == ["aota.invoke"]
    assert result["child_creation_time_affects_result"] == "no"
    assert result["SESSION_ID_VERIFIED_PER_INVOCATION"] == "yes"
    assert result["CALL_ID_VERIFIED_PER_INVOCATION"] == "yes"
    assert result["ONE_SESSION_ONE_LOGICAL_AF_CONTEXT"] == "yes"
    assert result["PROJECT_ROOT_DIRECTORY_REQUIREMENT_SUPERSEDED"] == "yes"
    assert result["CHILD_POSTDATES_SESSION_REQUIREMENT_SUPERSEDED"] == "yes"
    assert result["PER_SESSION_CHILD_PROCESS_REQUIREMENT_SUPERSEDED"] == "yes"
    assert result["SHARED_CHILD_ALLOWED"] == "yes"
    assert result["NATIVE_SESSION_DIRECTORY_SEMANTICS_PRESERVED"] == "yes"
    assert result["SESSION_DIRECTORY_MUST_EQUAL_PROJECT_ROOT"] == "no"
    assert result["CHILD_MUST_POSTDATE_SESSION"] == "no"
    assert result["PHYSICAL_ONE_CHILD_PER_SESSION_REQUIRED"] == "no"
    assert result["CHILD_PID_IS_SESSION_AUTHORITY"] == "no"
    assert result["CHILD_BIRTH_TIME_IS_SESSION_AUTHORITY"] == "no"
    assert result["SESSION_DIRECTORY_IS_PROJECT_AUTHORITY"] == "no"
    assert result["MODEL_SUPPLIES_SESSION_ID"] == "no"
    assert result["MODEL_MINTS_AUTHORITY"] == "no"
    assert result["GLOBAL_CURRENT_SESSION"] == "no"
    assert result["GLOBAL_BOUND_DELEGATE"] == "no"
    assert result["LATEST_SESSION_INFERENCE"] == "no"
    assert result["LATEST_TOKEN_INFERENCE"] == "no"
    assert result["CLOSEST_TIMESTAMP_INFERENCE"] == "no"
    assert result["CHILD_BIRTH_ORDERING_INFERENCE"] == "no"
    assert result["PROMPT_ORDERING_INFERENCE"] == "no"
    assert result["SINGLE_PROCESS_ASSUMPTION"] == "no"
    assert result["W1_ROLE_BOOTSTRAP_OBSERVED"] == "PASS"
    assert result["ROLE_BOOTSTRAP_NULL_ARGUMENTS_RESULT"] == "REJECTED"
    assert result["ROLE_BOOTSTRAP_EMPTY_OBJECT_ARGUMENTS_RESULT"] == "PASS"
    assert result["ROLE_BOOTSTRAP_NULL_ARGUMENT_FINDING"] == "NON_BLOCKING_INVOCATION_SHAPE_FINDING"
    assert result["W1_ROLE_BOOTSTRAP_RERUN"] == "no"


def test_native_session_directory_matches_child_cwd_without_project_authority() -> None:
    result = validate_p0l_live_child_provenance(
        _p0l_facts(
            session_directory="/home/latios/workspace",
            child_cwd="/home/latios/workspace",
            context_state_before_explicit_bind="unbound",
            context_id_before_explicit_bind=None,
            project_authority_before_explicit_bind="no",
            plan_authority_before_explicit_bind="no",
        )
    )
    assert result["status"] == "PASS"
    assert result["project_authority_before_explicit_bind"] == "no"
    assert result["plan_authority_before_explicit_bind"] == "no"
    assert result["context_before_explicit_bind"] == "unbound"


def test_exact_plugin_session_and_call_correlation_passes() -> None:
    result = validate_p0l_live_child_provenance(_p0l_facts())
    assert result["session_id_sha256"] == "a" * 64
    assert result["call_id_sha256"] == "b" * 64


def test_pinned_host_directory_instance_equivalence_passes() -> None:
    result = validate_p0l_live_child_provenance(
        _p0l_facts(
            session_directory="/directory/A",
            child_cwd="/directory/B",
            session_directory_instance_id="pinned-instance-1",
            child_directory_instance_id="pinned-instance-1",
        )
    )
    assert result["native_directory_instance_correlation"] == "PASS"


def test_carrier_from_session_a_is_denied_in_session_b() -> None:
    session_b = "d" * 64
    with pytest.raises(ActivationGateError) as error:
        validate_p0l_live_child_provenance(
            _p0l_facts(
                session_id_sha256=session_b,
                plugin_session_id_sha256=session_b,
                bridge_session_id_sha256=session_b,
                child_invocation_session_id_sha256=session_b,
                af_session_id_sha256=session_b,
                af_logical_context_session_id_sha256=session_b,
            )
        )
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE
    assert "bridge_carrier_session_id_sha256" in str(error.value)


def test_session_a_with_call_id_b_is_denied() -> None:
    call_b = "d" * 64
    with pytest.raises(ActivationGateError) as error:
        validate_p0l_live_child_provenance(
            _p0l_facts(
                call_id_sha256=call_b,
                plugin_call_id_sha256=call_b,
                bridge_call_id_sha256=call_b,
                child_invocation_call_id_sha256=call_b,
            )
        )
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE
    assert "bridge_carrier_call_id_sha256" in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        {"session_directory": "/directory/A", "child_cwd": "/directory/B"},
        {"plugin_call_id_sha256": "d" * 64},
        {"bridge_carrier_replayed": True},
        {"bridge_carrier_substituted": True},
        {"af_logical_context_id_sha256": "d" * 64},
        {"project_authority_before_explicit_bind": "yes"},
        {"plan_authority_before_explicit_bind": "yes"},
    ],
)
def test_directory_routing_and_unbound_authority_mismatches_fail_security(
    change: dict[str, object],
) -> None:
    with pytest.raises(ActivationGateError) as error:
        validate_p0l_live_child_provenance(_p0l_facts(**change))
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE


@pytest.mark.parametrize(
    "change",
    [
        {"session_directory": "/directory/A", "child_cwd": "/directory/B"},
        {"plugin_call_id_sha256": "d" * 64},
    ],
)
def test_corrected_directory_or_routing_failure_remains_rollback_eligible(
    change: dict[str, object],
) -> None:
    with pytest.raises(ActivationGateError) as error:
        validate_p0l_live_child_provenance(_p0l_facts(**change))
    calls = 0

    def rollback() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "PASS"}

    result = apply_failure_policy(
        error.value.failure_class,
        production_healthy=True,
        provenance_valid=False,
        rollback=rollback,
    )
    assert calls == 1
    assert result.rollback_required is True
    assert result.rollback_executed is True


@pytest.mark.parametrize(
    "change",
    [
        {"child_runtime_root_matches_candidate": False},
        {"child_parent_matches_opencode": False},
        {"genuine_headed_w1_session": False},
        {"child_candidate_identity_matches": False},
        {"tools_list": ["aota.invoke", "other.tool"]},
    ],
)
def test_p0l_wrong_child_provenance_or_tool_surface_fails_security(change: dict[str, object]) -> None:
    with pytest.raises(ActivationGateError) as error:
        validate_p0l_live_child_provenance(_p0l_facts(**change))
    assert error.value.failure_class == ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE
