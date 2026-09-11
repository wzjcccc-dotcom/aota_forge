"""AF #48 M1/W1 — Agent-facing Contract Freeze (W1 source construction).

Proves contract frozen per spec §4-§16. Focused deterministic tests only,
no W2/W3/W4 implementation, no new execution engine.

Covers:
- role naming (project-steward, CanonicalRole preservation)
- common surface (search, read, write, terminal, handoff, task.start, task.return)
- lifecycle separation (handoff != dispatch, etc.)
- envelope/payload ownership
- internal reuse (task.start -> execution.task_start, task.return -> completion)
- architecture invariants (ONE_CONTROL_PLANE, ONE_AGENT_FACING_AOTA_MCP_TOOL)
- WorkSemanticProjection boundary
- task_main operation classification
"""

from __future__ import annotations

import pytest

from aota_forge.work_plane.roles import AgentWorkRole, WORK_ROLES, WORK_ROLE_SET
from aota_forge.core.execution.roles import CanonicalRole, CANONICAL_ROLE_SET

import aota_forge.work_plane.agent_facing_contract as afc
from aota_forge.work_plane.agent_facing_contract import AgentToolContract


# ---------------------------------------------------------------------------
# Role naming
# ---------------------------------------------------------------------------


class TestRoleNaming:
    def test_agent_work_role_includes_project_steward(self):
        assert "project-steward" in WORK_ROLES
        assert AgentWorkRole.PROJECT_STEWARD.value == "project-steward"
        assert AgentWorkRole.is_valid("project-steward") is True
        assert afc.AGENT_WORK_ROLE_STEWARD_NAME == "project-steward"
        assert afc.COMMON_ROLES == ("task-main", "analyst", "coder", "reviewer", "project-steward")
        assert afc.AGENT_WORK_ROLE_COUNT == 5
        assert afc.NO_SIXTH_WORK_ROLE is True

    def test_one_shot_return_roles_include_project_steward(self):
        assert "project-steward" in afc.ONE_SHOT_ROLES
        assert "coder" in afc.ONE_SHOT_ROLES
        assert "analyst" in afc.ONE_SHOT_ROLES
        assert "reviewer" in afc.ONE_SHOT_ROLES
        assert "task-main" not in afc.ONE_SHOT_ROLES
        # validate helper
        assert afc.is_task_return_caller("project-steward") is True
        assert afc.is_task_return_caller(AgentWorkRole.PROJECT_STEWARD) is True
        assert afc.is_task_return_caller("coder") is True
        assert afc.is_task_return_caller("task-main") is False

    def test_agent_facing_contract_does_not_use_steward_as_agent_work_role(self):
        assert afc.AGENT_FACING_CONTRACT_USES_STEWARD_AS_AGENT_WORK_ROLE is False
        # "steward" alone is not a valid AgentWorkRole
        assert AgentWorkRole.is_valid("steward") is False
        assert "steward" not in WORK_ROLE_SET
        # but "project-steward" is valid
        assert AgentWorkRole.is_valid("project-steward") is True

    def test_canonical_role_steward_compatibility_preserved(self):
        # CanonicalRole still has steward as internal execution role
        assert "steward" in CANONICAL_ROLE_SET
        assert CanonicalRole.STEWARD.value == "steward"
        assert CanonicalRole.is_valid("steward") is True
        # compatibility flag
        assert afc.CANONICAL_ROLE_STEWARD_COMPATIBILITY_PRESERVED is True
        # AgentWorkRole and CanonicalRole remain distinct types
        assert AgentWorkRole.CODER.value == CanonicalRole.CODER.value == "coder"
        assert type(AgentWorkRole.CODER) is not type(CanonicalRole.CODER)
        # Foreign Enum rejected by AgentWorkRole validation
        with pytest.raises(TypeError):
            import aota_forge.work_plane.roles as rm

            rm.validate_agent_work_role(CanonicalRole.CODER)


# ---------------------------------------------------------------------------
# Common surface
# ---------------------------------------------------------------------------


class TestCommonSurface:
    def test_common_agent_tool_families_exactly_five(self):
        assert afc.COMMON_AGENT_TOOL_FAMILIES == ("search", "read", "write", "terminal", "handoff")
        assert len(afc.COMMON_AGENT_TOOL_FAMILIES) == 5
        assert set(afc.COMMON_AGENT_TOOL_FAMILIES) == {"search", "read", "write", "terminal", "handoff"}

    def test_task_main_extra_and_one_shot_extra(self):
        assert afc.TASK_MAIN_EXTRA == "task.start"
        assert afc.ONE_SHOT_ROLE_EXTRA == "task.return"
        # task.start task-main-only
        assert afc.TASK_START_CALLER == "task-main"
        assert afc.is_task_start_caller("task-main") is True
        assert afc.is_task_start_caller("coder") is False
        assert afc.is_task_start_caller(AgentWorkRole.TASK_MAIN) is True
        assert afc.is_task_start_caller(AgentWorkRole.CODER) is False

    def test_task_return_one_shot_only(self):
        with pytest.raises(ValueError):
            afc.validate_task_start_caller("coder")
        with pytest.raises(ValueError):
            afc.validate_task_return_caller("task-main")
        # valid
        assert afc.validate_task_start_caller("task-main") == "task-main"
        assert afc.validate_task_return_caller("project-steward") == "project-steward"

    def test_all_seven_contracts_exist(self):
        assert len(afc.ALL_AGENT_CONTRACTS) == 7
        assert afc.ALL_AGENT_CONTRACT_NAMES == frozenset(
            {"search", "read", "write", "terminal", "handoff", "task.start", "task.return"}
        )
        for name in afc.ALL_AGENT_CONTRACT_NAMES:
            c = afc.get_agent_contract(name)
            assert isinstance(c, AgentToolContract)
            assert c.name == name
            assert afc.contract_maps_to_existing_seam(c) is True

    def test_contract_has_all_required_dimensions(self):
        for c in afc.ALL_AGENT_CONTRACTS:
            # Each must have all 9 dimensions non-empty
            assert c.caller_applicable_roles
            assert c.llm_supplied_input
            assert c.control_plane_supplied_input
            assert c.control_plane_validation
            assert c.output
            assert c.explicit_non_responsibilities
            assert c.internal_reused_seam
            assert c.authority_source

    def test_no_sixth_work_role(self):
        assert len(afc.COMMON_ROLES) == 5
        assert "project-steward" in afc.COMMON_ROLES
        assert "steward" not in afc.COMMON_ROLES


# ---------------------------------------------------------------------------
# Lifecycle separation
# ---------------------------------------------------------------------------


class TestLifecycleSeparation:
    def test_handoff_not_dispatch_and_not_termination(self):
        assert afc.HANDOFF_DISPATCHES_WORKER is False
        assert afc.HANDOFF_TERMINATES_TASK is False
        # Also check handoff contract explicit non-responsibilities mention
        assert "handoff does not dispatch Worker" in afc.HANDOFF_CONTRACT.explicit_non_responsibilities
        assert any("does not terminate" in s for s in afc.HANDOFF_CONTRACT.explicit_non_responsibilities)

    def test_task_start_not_semantic_result_creation(self):
        assert afc.TASK_START_IS_NOT_SEMANTIC_RESULT_CREATION is True
        assert afc.TASK_RETURN_CREATES_WORK_SEMANTICS is False

    def test_task_return_not_semantic_work_creation(self):
        assert afc.TASK_RETURN_CREATES_WORK_SEMANTICS is False
        assert afc.TASK_RETURN_IS_NOT_HANDOFF_WRITE is True

    def test_task_return_not_execution_task_result(self):
        assert afc.TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT is True
        assert afc.TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT_ALIAS is True
        # execution.task_result is fetch concept, not terminal return
        assert afc.TASK_RETURN_INTERNAL_REUSED_SEAM != "execution.task_result"

    def test_worker_may_handoff_write_without_terminating(self):
        assert afc.WORKER_MAY_HANDOFF_WRITE_WITHOUT_TERMINATING is True
        assert afc.ONLY_TASK_RETURN_MARKS_TERMINAL_RETURN is True


# ---------------------------------------------------------------------------
# Envelope / payload ownership
# ---------------------------------------------------------------------------


class TestEnvelopePayloadOwnership:
    def test_llm_semantic_fields_separated_from_control_owned(self):
        assert afc.HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD is True
        assert afc.CONTROL_METADATA_MUST_NOT_BE_FORCED_INTO_TASK_HANDOFF_SEMANTIC_FIELDS is True
        assert afc.HANDOFF_CONTROL_ENVELOPE_SEPARATE is True
        assert afc.TASK_HANDOFF_SEMANTIC_BOUNDARY_PRESERVED is True

    def test_task_handoff_does_not_become_mechanical_dump(self):
        assert afc.HANDOFF_CONTROL_ENVELOPE_IS_TASK_HANDOFF_SEMANTIC_PAYLOAD is False
        # TaskHandoff forbidden mechanical fields still enforced
        from aota_forge.work_plane.handoff import TaskHandoff, FORBIDDEN_MECHANICAL_FIELDS

        with pytest.raises(ValueError):
            TaskHandoff.from_dict(
                {
                    "work_role": "coder",
                    "task_kind": "test",
                    "objective": "obj",
                    "bounded_scope": "scope",
                    "validation_expectations": ["v"],
                    "semantic_stop_expectations": ["s"],
                    "package_id": "should_fail",
                }
            )
        # Ensure forbidden set still contains mechanical fields
        assert "package_id" in FORBIDDEN_MECHANICAL_FIELDS

    def test_llm_cannot_author_trusted_control_fields(self):
        assert afc.LLM_MUTATES_CONTROL_FIELDS is False

    def test_control_plane_does_not_rewrite_llm_semantics(self):
        assert afc.CONTROL_PLANE_REWRITES_LLM_SEMANTICS is False


# ---------------------------------------------------------------------------
# Internal reuse
# ---------------------------------------------------------------------------


class TestInternalReuse:
    def test_task_start_maps_to_existing_execution_seam(self):
        assert afc.TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM is True
        assert afc.TASK_START_FAÇADE_MAPS_TO_EXISTING_EXECUTION_SEAM is True
        assert "execution.task_start" in afc.TASK_START_INTERNAL_REUSED_SEAM
        assert afc.EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED is False

    def test_task_return_maps_to_completion_seam(self):
        assert afc.TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS is True
        assert "completion" in afc.TASK_RETURN_INTERNAL_REUSED_SEAM
        assert "wakeup" in afc.TASK_RETURN_INTERNAL_REUSED_SEAM

    def test_work_semantic_projection_not_required_as_agent_prestep(self):
        assert afc.WORK_SEMANTIC_PROJECTION_AGENT_FACING_REQUIRED is False
        assert afc.PLAN_TO_WORK_SEMANTIC_PROJECTION_REQUIRED_AS_AGENT_PRESTEP is False

    def test_internal_compatibility_reuse_allowed(self):
        assert afc.WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED is True
        assert afc.INTERNAL_COMPATIBILITY_SEAMS_REUSED is True
        assert afc.WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_SEAMS_REUSED is True

    def test_all_contracts_map_to_existing_seams(self):
        for c in afc.ALL_AGENT_CONTRACTS:
            assert c.internal_reused_seam


# ---------------------------------------------------------------------------
# Architecture invariants
# ---------------------------------------------------------------------------


class TestArchitectureInvariants:
    def test_one_control_plane(self):
        assert afc.ONE_CONTROL_PLANE is True
        assert afc.AF_CONTROL_PLANE_COUNT == 1
        assert afc.CANONICAL_OPERATION_DISPATCH_PLANE_COUNT == 1

    def test_one_agent_facing_aota_mcp_tool(self):
        assert afc.ONE_AGENT_FACING_AOTA_MCP_TOOL is True
        assert afc.ONE_AGENT_FACING_AOTA_MCP_TOOL_REQUIRED is True
        assert afc.AGENT_FACING_AOTA_TOOL == "aota.invoke"
        assert afc.AGENT_FACING_AOTA_MCP_TOOL_NAME == "aota.invoke"

    def test_skill_not_authority(self):
        assert afc.SKILL_IS_AUTHORITY is False

    def test_tool_visibility_not_authority(self):
        assert afc.TOOL_VISIBILITY_IS_AUTHORITY is False
        assert afc.ROLE_TOOL_SURFACE_IS_AUTHORITY is False
        # Also check work_plane/tool_surface flags
        from aota_forge.work_plane.tool_surface import (
            ROLE_TOOL_SURFACE_IS_AUTHORITY as RTSIA,
            TOOL_EXPOSURE_IS_AUTHORITY as TEIA,
        )

        assert RTSIA is False
        assert TEIA is False

    def test_control_plane_assist_and_guard(self):
        assert afc.CONTROL_PLANE_ROLE == "assist_and_guard"
        assert afc.CONTROL_PLANE_IS_WORKFLOW_BRAIN is False
        assert afc.CONTROL_PLANE_IS_SEMANTIC_INTERPRETER is False
        assert afc.CONTROL_PLANE_REPLACES_LLM_REASONING is False
        assert "semantic_interpretation" in afc.LLM_OWNS
        assert "identity" in afc.CONTROL_PLANE_OWNS

    def test_no_new_subsystems(self):
        assert afc.NEW_EXECUTION_ENGINE_CREATED is False
        assert afc.NEW_AUTHORITY_ENGINE_CREATED is False
        assert afc.NEW_RESULT_ONTOLOGY_CREATED is False
        assert afc.NEW_COORDINATOR_CREATED is False
        assert afc.SECOND_CONTROL_PLANE_CREATED is False
        assert afc.POLICY_YAML_EXTERNALIZATION_PERFORMED is False
        assert afc.NEW_TASK_STATE_MACHINE_CREATED is False

    def test_terminal_is_bounded_restricted(self):
        assert afc.TERMINAL_KIND == "bounded_restricted_terminal"
        assert afc.TERMINAL_IS_RAW_BASH is False
        assert afc.TERMINAL_ARGV_STYLE is True
        assert afc.TERMINAL_NO_SHELL_TRUE is True
        assert afc.TERMINAL_TRUSTED_CATALOG is True
        assert afc.TERMINAL_ROLE_COMMAND_POLICY is True
        assert afc.NO_POLICY_YAML_EXTERNALIZATION is True


# ---------------------------------------------------------------------------
# Search / Read / Write specific frozen values
# ---------------------------------------------------------------------------


class TestSearchReadWriteFrozen:
    def test_search_frozen(self):
        assert afc.AUTHORIZED_PROJECT_SEARCH == "broad"
        assert afc.CROSS_PROJECT_SEARCH_FAIL_CLOSED is True
        assert afc.SEARCH_RESULT_IS_AUTHORITY is False
        assert afc.ANALYST_IS_READ_PERMISSION_PROXY is False
        assert afc.SEARCH_THRESHOLD_GATE_REQUIRED is False

    def test_read_frozen(self):
        assert afc.AUTHORIZED_PROJECT_READ == "broad"
        assert afc.CROSS_PROJECT_READ_FAIL_CLOSED is True
        assert afc.READ_RESULT_IS_PLAN_AUTHORITY is False

    def test_write_frozen_conceptual(self):
        assert afc.AUTHORIZED_PROJECT_WRITE == "role_and_scope_bounded"
        assert afc.BROAD_READ is True
        assert afc.NARROW_WRITE is True
        assert afc.TASK_MAIN_PRODUCT_SOURCE_WRITE_ALLOWED is False
        assert afc.ANALYST_PRODUCT_SOURCE_WRITE_ALLOWED is False
        assert afc.REVIEWER_PRODUCT_SOURCE_WRITE_ALLOWED is False
        assert afc.CODER_ASSIGNED_PROJECT_WORKTREE_PRODUCT_WRITE_ALLOWED is True
        assert afc.PROJECT_STEWARD_GOVERNED_MUTATION_ONLY is True

    def test_handoff_modes_and_views(self):
        assert afc.HANDOFF_WRITE_MODES == ("milestone", "work_item", "result")
        assert afc.HANDOFF_OPEN_VIEWS == ("card", "full")
        assert afc.HANDOFF_ACTIONS == ("write", "open")


# ---------------------------------------------------------------------------
# Result / Card
# ---------------------------------------------------------------------------


class TestResultCard:
    def test_result_write_count(self):
        assert afc.WORKER_RESULT_FULL_WRITE_COUNT_NORMAL == 1
        assert afc.RESULT_FULL_WRITE_COUNT_NORMAL == 1
        assert afc.WORKER_AUTHORS_RESULT_CARD is False
        assert afc.RESULT_CARD_GENERATION == "deterministic_from_full_result"

    def test_result_reuse(self):
        assert "CanonicalResult" in afc.RESULT_INTERNAL_REUSED_SEAM
        assert "WorkerResultCard" in afc.RESULT_INTERNAL_REUSED_SEAM
        assert afc.RESULT_NORMAL_TASK_MAIN_CARD_EXTRA_READ_CALL == 0


# ---------------------------------------------------------------------------
# Operation descriptor / ingress
# ---------------------------------------------------------------------------


class TestOperationDescriptor:
    def test_single_descriptor_authority(self):
        assert afc.CANONICAL_DESCRIPTOR_AUTHORITY == ".aota/contracts/operations.yaml"
        assert afc.OPERATION_DESCRIPTOR_AUTHORITY_COUNT == 1
        assert afc.CANONICAL_OPERATION_DISPATCH_PLANE_COUNT == 1

    def test_no_live_registration_without_provider(self):
        assert afc.DO_NOT_REGISTER_AGENT_FACING_LIVE_OPERATION_WITHOUT_PROVIDER is True
        assert afc.W1_IS_CONTRACT_FREEZE_NOT_LIVE_REGISTRATION is True


# ---------------------------------------------------------------------------
# Task main operation classification
# ---------------------------------------------------------------------------


class TestTaskMainClassification:
    def test_all_four_classified(self):
        ops = afc.TASK_MAIN_OPERATION_CLASSIFICATION
        assert set(ops.keys()) == {
            "task_main.activate_milestone",
            "task_main.recover_coordinator",
            "task_main.advance_once",
            "task_main.submit_work_projection",
        }

    def test_submit_work_projection_not_agent_facing_required(self):
        assert afc.TASK_MAIN_SUBMIT_WORK_PROJECTION_AGENT_FACING_REQUIRED is False
        assert afc.TASK_MAIN_SUBMIT_WORK_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED is True
        # classification string must mention compatibility / not required
        cls = afc.TASK_MAIN_OPERATION_CLASSIFICATION["task_main.submit_work_projection"]
        assert "Agent-facing required = no" in cls or "not required" in cls.lower()


# ---------------------------------------------------------------------------
# Façade / internal boundary acceptance flags
# ---------------------------------------------------------------------------


class TestFacadeInternalBoundary:
    def test_all_boundary_flags(self):
        assert afc.AGENT_FACING_CONTRACT_DOES_NOT_REQUIRE_INTERNAL_TYPE_REWRITE is True
        assert afc.HANDOFF_CONTROL_ENVELOPE_SEPARATE_FROM_SEMANTIC_PAYLOAD is True
        assert afc.CONTROL_METADATA_MUST_NOT_BE_FORCED_INTO_TASK_HANDOFF_SEMANTIC_FIELDS is True
        assert afc.TASK_START_AGENT_FACADE_REUSES_EXISTING_EXECUTION_START_SEAM is True
        assert afc.EXECUTION_TASK_START_DIRECT_AGENT_INTERFACE_REQUIRED is False
        assert afc.TASK_RETURN_IS_NOT_EXECUTION_TASK_RESULT is True
        assert afc.TASK_RETURN_REUSES_EXISTING_COMPLETION_FINALIZATION_WAKEUP_SEAMS is True
        assert afc.WORK_SEMANTIC_PROJECTION_AGENT_FACING_REQUIRED is False
        assert afc.WORK_SEMANTIC_PROJECTION_INTERNAL_COMPATIBILITY_ALLOWED is True
        assert afc.INTERNAL_COMPATIBILITY_SEAMS_REUSED is True


# ---------------------------------------------------------------------------
# Foundation reuse / expected change size
# ---------------------------------------------------------------------------


class TestFoundationReuse:
    def test_reuse_markers(self):
        assert afc.FOUNDATION_REUSE_EXPECTED is True
        assert afc.EXPECTED_ARCHITECTURE_CHANGE == "boundary_convergence"
        assert afc.EXPECTED_SOURCE_CHANGE_CLASS == "bounded_medium_small"
        assert afc.LARGE_SCALE_CORE_REWRITE_REQUIRED is False
