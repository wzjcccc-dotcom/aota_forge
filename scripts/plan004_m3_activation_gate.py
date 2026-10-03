"""Small acceptance seam for the Plan #004 M3 lazy-MCP activation gates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
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
    "directory_instance_matches_w1",
    "child_parent_matches_opencode",
    "child_cwd_matches_directory_instance",
    "child_command_matches_candidate_wrapper",
    "child_runtime_root_matches_candidate",
    "child_source_provenance_matches",
    "child_candidate_identity_matches",
)


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
    """Validate the child only after a genuine headed W1 MCP use created it."""
    session_hash = facts.get("session_hash")
    missing = [name for name in (*_P0L_CHILD_FACTS, "session_hash", "child_pid", "child_ppid", "tools_list") if name not in facts]
    failed = [name for name in _P0L_CHILD_FACTS if facts.get(name) is not True]
    if not isinstance(session_hash, str) or re.fullmatch(r"[0-9a-f]{64}", session_hash) is None:
        failed.append("session_hash")
    if not isinstance(facts.get("child_pid"), int) or facts["child_pid"] <= 0:
        failed.append("child_pid")
    if not isinstance(facts.get("child_ppid"), int) or facts["child_ppid"] <= 0:
        failed.append("child_ppid")
    if facts.get("tools_list") != ["aota.invoke"]:
        failed.append("tools_list")
    if missing or failed:
        names = sorted(set(missing + failed))
        raise ActivationGateError(
            ActivationFailureClass.SECURITY_OR_PROVENANCE_FAILURE,
            "P0L_LIVE_CHILD_PROVENANCE",
            "live child provenance checks failed: " + ", ".join(names),
        )
    return {
        "status": "PASS",
        "gate": "P0L_LIVE_CHILD_PROVENANCE",
        "tools_list": ["aota.invoke"],
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
