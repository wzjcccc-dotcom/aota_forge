"""Small acceptance seam for the Plan #004 M3 lazy-MCP activation gates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import re


class ActivationFailureClass(StrEnum):
    SECURITY_OR_PROVENANCE_FAILURE = "SECURITY_OR_PROVENANCE_FAILURE"
    PRODUCTION_UNHEALTHY = "PRODUCTION_UNHEALTHY"
    FUNCTIONAL_FAILURE = "FUNCTIONAL_FAILURE"
    MANUAL_GATE = "MANUAL_GATE"
    UNKNOWN_ACTIVATION_FAILURE = "UNKNOWN_ACTIVATION_FAILURE"


class ActivationGateError(RuntimeError):
    def __init__(self, failure_class: ActivationFailureClass, stage: str, message: str) -> None:
        self.failure_class = failure_class
        self.stage = stage
        super().__init__(message)


class RollbackFailed(RuntimeError):
    """Rollback did not return verified recovery evidence."""

    restored = False


@dataclass(frozen=True)
class FailureDisposition:
    failure_class: ActivationFailureClass
    rollback_required: bool
    rollback_executed: bool
    rollback_health: str
    rollback_reason: str | None
    restored: bool


_P0_STATIC_FACTS = (
    "opencode_4096_healthy",
    "opencode_4095_healthy",
    "bridge_healthy",
    "service_config_runtime_matches_candidate",
    "mcp_command_runtime_matches_candidate",
    "plugin_config_hash_matches",
    "bridge_config_hash_matches",
    "candidate_runtime_root_exists",
    "candidate_runtime_is_immutable_and_not_worktree",
    "candidate_manifest_digest_matches",
    "candidate_source_provenance_matches",
    "project_manifest_present",
    "operations_contract_present",
    "operation_descriptor_count_is_43",
    "configured_mcp_surface_is_single_aota_entry",
)

_P0L_CHILD_FACTS = (
    "genuine_headed_w1_session",
    "original_w1_session_preserved",
    "child_parent_matches_opencode",
    "child_command_matches_candidate_wrapper",
    "child_runtime_root_matches_candidate",
    "child_source_provenance_matches",
    "child_candidate_identity_matches",
    "plugin_hook_is_tool_execute_before",
    "bridge_carrier_verified",
    "bridge_session_id_verified_per_invocation",
    "bridge_call_id_verified_per_invocation",
    "bridge_carrier_session_correlation_verified",
    "bridge_carrier_call_id_correlation_verified",
    "bridge_one_time_semantics_preserved",
    "af_context_selected_from_verified_session",
    "one_session_one_logical_af_context",
)

_P0L_ARCHITECTURAL_CONTRACT = {
    "PROJECT_ROOT_DIRECTORY_REQUIREMENT_SUPERSEDED": "yes",
    "CHILD_POSTDATES_SESSION_REQUIREMENT_SUPERSEDED": "yes",
    "PER_SESSION_CHILD_PROCESS_REQUIREMENT_SUPERSEDED": "yes",
    "SHARED_CHILD_ALLOWED": "yes",
    "NATIVE_SESSION_DIRECTORY_SEMANTICS_PRESERVED": "yes",
    "SESSION_DIRECTORY_MUST_EQUAL_PROJECT_ROOT": "no",
    "CHILD_MUST_POSTDATE_SESSION": "no",
    "PHYSICAL_ONE_CHILD_PER_SESSION_REQUIRED": "no",
    "CHILD_PID_IS_SESSION_AUTHORITY": "no",
    "CHILD_BIRTH_TIME_IS_SESSION_AUTHORITY": "no",
    "SESSION_DIRECTORY_IS_PROJECT_AUTHORITY": "no",
    "SESSION_ID_VERIFIED_PER_INVOCATION": "yes",
    "CALL_ID_VERIFIED_PER_INVOCATION": "yes",
    "ONE_SESSION_ONE_LOGICAL_AF_CONTEXT": "yes",
    "MODEL_SUPPLIES_SESSION_ID": "no",
    "MODEL_MINTS_AUTHORITY": "no",
    "GLOBAL_CURRENT_SESSION": "no",
    "GLOBAL_BOUND_DELEGATE": "no",
    "LATEST_SESSION_INFERENCE": "no",
    "LATEST_TOKEN_INFERENCE": "no",
    "CLOSEST_TIMESTAMP_INFERENCE": "no",
    "CHILD_BIRTH_ORDERING_INFERENCE": "no",
    "PROMPT_ORDERING_INFERENCE": "no",
    "SINGLE_PROCESS_ASSUMPTION": "no",
}

_PRESERVED_W1_BOOTSTRAP_FINDING = {
    "W1_ROLE_BOOTSTRAP_OBSERVED": "PASS",
    "ROLE_BOOTSTRAP_NULL_ARGUMENTS_RESULT": "REJECTED",
    "ROLE_BOOTSTRAP_EMPTY_OBJECT_ARGUMENTS_RESULT": "PASS",
    "ROLE_BOOTSTRAP_NULL_ARGUMENT_FINDING": "NON_BLOCKING_INVOCATION_SHAPE_FINDING",
    "W1_ROLE_BOOTSTRAP_RERUN": "no",
}

_V7R1_CANDIDATE_IDENTITY = {
    "candidate_version": "m3-preparation-v7r1",
    "candidate_manifest_digest": "sha256:47797ea694538d51e73a2e90b4e6b191e4e7a725bbd2f8b42284bf3161be416f",
    "candidate_runtime_source_frontier": "a3a8f8544b892b9d31f847288b1e96eacde635fb",
    "candidate_package_commit": "81d375f49969e49b85b591111eedb94b28386c14",
}


def validate_v7r1_candidate_immutability(facts: Mapping[str, object]) -> dict[str, object]:
    """Require the approved v7r1 identity and a byte-identical, unrepackaged candidate."""
    keys = (*_V7R1_CANDIDATE_IDENTITY, "candidate_changed", "candidate_repackaged")
    missing = [name for name in keys if name not in facts]
    failed = [
        name
        for name, expected in _V7R1_CANDIDATE_IDENTITY.items()
        if facts.get(name) != expected
    ]
    failed.extend(
        name
        for name in ("candidate_changed", "candidate_repackaged")
        if facts.get(name) is not False
    )
    if missing or failed:
        names = sorted(set(missing + failed))
        raise ActivationGateError(
            ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE,
            "V7R1_CANDIDATE_IMMUTABILITY",
            "candidate immutability checks failed: " + ", ".join(names),
        )
    return {
        "status": "PASS",
        **_V7R1_CANDIDATE_IDENTITY,
        "candidate_changed": "no",
        "candidate_repackaged": "no",
    }


def validate_p0_static_provenance(facts: Mapping[str, object]) -> dict[str, object]:
    """Validate only static deployment provenance; child absence is not a P0 failure."""
    missing = [name for name in _P0_STATIC_FACTS if name not in facts]
    failed = [name for name in _P0_STATIC_FACTS if facts.get(name) is not True]
    if missing or failed:
        names = sorted(set(missing + failed))
        raise ActivationGateError(
            ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE,
            "P0_STATIC_PROVENANCE",
            "static provenance checks failed: " + ", ".join(names),
        )
    return {
        "status": "PASS",
        "gate": "P0_STATIC_PROVENANCE",
        "directory_instance_required": False,
        "mcp_child_required": False,
    }


def validate_p0l_live_child_provenance(facts: Mapping[str, object]) -> dict[str, object]:
    """Prove exact headed invocation routing through a shared MCP child."""
    correlation_fields = (
        "session_id_sha256",
        "call_id_sha256",
        "plugin_session_id_sha256",
        "plugin_call_id_sha256",
        "bridge_carrier_session_id_sha256",
        "bridge_carrier_call_id_sha256",
        "bridge_session_id_sha256",
        "bridge_call_id_sha256",
        "child_invocation_session_id_sha256",
        "child_invocation_call_id_sha256",
        "af_session_id_sha256",
        "af_logical_context_session_id_sha256",
        "af_logical_context_id_sha256",
        "af_expected_logical_context_id_sha256",
    )
    required_fields = (
        *_P0L_CHILD_FACTS,
        *correlation_fields,
        "session_directory",
        "child_cwd",
        "child_pid",
        "child_ppid",
        "tools_list",
        "bridge_carrier_replayed",
        "bridge_carrier_substituted",
        "context_state_before_explicit_bind",
        "context_id_before_explicit_bind",
        "project_authority_before_explicit_bind",
        "plan_authority_before_explicit_bind",
    )
    missing = [name for name in required_fields if name not in facts]
    failed = [name for name in _P0L_CHILD_FACTS if facts.get(name) is not True]

    for name in correlation_fields:
        value = facts.get(name)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            failed.append(name)

    session_hash = facts.get("session_id_sha256")
    call_hash = facts.get("call_id_sha256")
    exact_correlations = {
        "plugin_session_id_sha256": session_hash,
        "bridge_carrier_session_id_sha256": session_hash,
        "bridge_session_id_sha256": session_hash,
        "child_invocation_session_id_sha256": session_hash,
        "af_session_id_sha256": session_hash,
        "af_logical_context_session_id_sha256": session_hash,
        "plugin_call_id_sha256": call_hash,
        "bridge_carrier_call_id_sha256": call_hash,
        "bridge_call_id_sha256": call_hash,
        "child_invocation_call_id_sha256": call_hash,
    }
    failed.extend(name for name, expected in exact_correlations.items() if facts.get(name) != expected)
    if facts.get("af_logical_context_id_sha256") != facts.get("af_expected_logical_context_id_sha256"):
        failed.append("af_logical_context_id_sha256")

    session_directory = facts.get("session_directory")
    child_cwd = facts.get("child_cwd")
    native_directory_match = (
        isinstance(session_directory, str)
        and bool(session_directory)
        and isinstance(child_cwd, str)
        and bool(child_cwd)
        and Path(session_directory).resolve(strict=False) == Path(child_cwd).resolve(strict=False)
    )
    instance_match = (
        isinstance(facts.get("session_directory_instance_id"), str)
        and bool(facts.get("session_directory_instance_id"))
        and facts.get("session_directory_instance_id") == facts.get("child_directory_instance_id")
    )
    if not native_directory_match and not instance_match:
        failed.append("native_session_directory_matches_child_cwd")

    if not isinstance(facts.get("child_pid"), int) or facts["child_pid"] <= 0:
        failed.append("child_pid")
    if not isinstance(facts.get("child_ppid"), int) or facts["child_ppid"] <= 0:
        failed.append("child_ppid")
    if facts.get("tools_list") != ["aota.invoke"]:
        failed.append("tools_list")
    if facts.get("bridge_carrier_replayed") is not False:
        failed.append("bridge_carrier_replayed")
    if facts.get("bridge_carrier_substituted") is not False:
        failed.append("bridge_carrier_substituted")
    if facts.get("context_state_before_explicit_bind") != "unbound":
        failed.append("context_state_before_explicit_bind")
    if facts.get("context_id_before_explicit_bind") is not None:
        failed.append("context_id_before_explicit_bind")
    if facts.get("project_authority_before_explicit_bind") != "no":
        failed.append("project_authority_before_explicit_bind")
    if facts.get("plan_authority_before_explicit_bind") != "no":
        failed.append("plan_authority_before_explicit_bind")
    if missing or failed:
        names = sorted(set(missing + failed))
        raise ActivationGateError(
            ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE,
            "P0L_LIVE_CHILD_PROVENANCE",
            "live invocation provenance checks failed: " + ", ".join(names),
        )
    return {
        "status": "PASS",
        "gate": "P0L_CORRECTED_INVOCATION_CORRELATION",
        "session_id_sha256": session_hash,
        "call_id_sha256": call_hash,
        "native_directory_instance_correlation": "PASS",
        "context_before_explicit_bind": "unbound",
        "project_authority_before_explicit_bind": "no",
        "plan_authority_before_explicit_bind": "no",
        "tools_list": ["aota.invoke"],
        "child_creation_time_affects_result": "no",
        **_P0L_ARCHITECTURAL_CONTRACT,
        **_PRESERVED_W1_BOOTSTRAP_FINDING,
    }


def apply_failure_policy(
    failure_class: ActivationFailureClass,
    *,
    production_healthy: bool | None,
    provenance_valid: bool | None,
    rollback: Callable[[], Mapping[str, object]],
) -> FailureDisposition:
    """Apply the bounded rollback policy and require verified recovery on rollback."""
    rollback_reason: str | None = None
    rollback_required = failure_class in {
        ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE,
        ActivationFailureClass.PRODUCTION_UNHEALTHY,
    }
    if failure_class == ActivationFailureClass.UNKNOWN_ACTIVATION_FAILURE:
        rollback_required = production_healthy is not True or provenance_valid is not True
        if rollback_required:
            rollback_reason = "UNKNOWN_SAFETY_STATE"

    if not rollback_required:
        return FailureDisposition(
            failure_class=failure_class,
            rollback_required=False,
            rollback_executed=False,
            rollback_health="NOT_REQUIRED",
            rollback_reason=None,
            restored=False,
        )

    try:
        result = rollback()
    except Exception as error:
        raise RollbackFailed("rollback raised before verified recovery") from error
    if result.get("status") != "PASS":
        raise RollbackFailed("rollback did not return PASS recovery evidence")
    return FailureDisposition(
        failure_class=failure_class,
        rollback_required=True,
        rollback_executed=True,
        rollback_health="PASS",
        rollback_reason=rollback_reason,
        restored=True,
    )
