"""AF #53 M1/W1 — Thin Control Plane / LLM-owned workflow boundary contract.

Focused deterministic contract tests only. They validate the W1 source-level
boundary freeze: ownership split, execution-lifecycle vs project-workflow
separation, task.start semantic surface + trusted enrichment, handoff
non-authority, reviewer genericity, legacy freeze, reverse-coupling
classification and foundation reuse/adapt boundary.

These tests do NOT construct the M1/W2 architecture drift-guard suite and do
NOT prove M2 thin composition or M3 real production dogfood behavior.
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path

import pytest

from aota_forge.work_plane import agent_facing_contract as afc
from aota_forge.work_plane import thin_path_boundary as tpb
from aota_forge.work_plane.task_facade import task_start

REPO_ROOT = tpb.repository_root()


def _module_file_exists(module_name: str) -> bool:
    rel = Path(*module_name.split("."))
    return (REPO_ROOT / rel).with_suffix(".py").is_file() or (
        REPO_ROOT / rel / "__init__.py"
    ).is_file()


def _resolve_dotted(dotted: str):
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:split]))
        except ModuleNotFoundError:
            continue
        obj = module
        for attr in parts[split:]:
            obj = getattr(obj, attr)
        return obj
    raise ImportError(f"cannot resolve {dotted}")


# ---------------------------------------------------------------------------
# AC1 — ownership split
# ---------------------------------------------------------------------------


class TestOwnershipSplit:
    def test_task_main_llm_owns_workflow_strategy(self):
        owned = set(tpb.TASK_MAIN_LLM_OWNS)
        assert {
            "plan_interpretation",
            "workflow_strategy",
            "decomposition",
            "sequencing",
            "delegation_strategy",
            "review_strategy",
            "repair_strategy",
            "semantic_judgment",
            "milestone_completion_judgment",
            "next_best_action_reasoning",
        } <= owned

    def test_control_plane_owns_trusted_mechanics(self):
        owned = set(tpb.CONTROL_PLANE_OWNS)
        assert {
            "identity",
            "trusted_project_binding",
            "authorization",
            "project_boundary",
            "worktree_boundary",
            "role_authority",
            "tool_authority",
            "runtime_configuration",
            "generic_task_execution_state",
            "session_identity",
            "parent_child_binding",
            "durability",
            "provenance",
            "resource_bounds",
            "mechanical_schema_validation",
        } <= owned

    def test_ownership_sets_disjoint(self):
        assert not set(tpb.TASK_MAIN_LLM_OWNS) & set(tpb.CONTROL_PLANE_OWNS)

    def test_control_plane_is_not_workflow_brain(self):
        assert tpb.CONTROL_PLANE_IS_WORKFLOW_BRAIN is False
        assert tpb.CONTROL_PLANE_IS_SEMANTIC_INTERPRETER is False
        assert tpb.CONTROL_PLANE_REPLACES_LLM_REASONING is False
        assert tpb.CONTROL_PLANE_ENFORCES_AUTHORITY_NOT_WORKFLOW_STRATEGY is True

    def test_workflow_strategy_change_does_not_require_control_plane_change(self):
        assert tpb.WORKFLOW_STRATEGY_CHANGE_REQUIRES_CONTROL_PLANE_SOURCE_CHANGE is False

    def test_control_plane_has_no_workflow_opinions(self):
        assert tpb.CONTROL_PLANE_REVIEW_FREQUENCY_OPINION is False
        assert tpb.CONTROL_PLANE_WORK_SEQUENCE_OPINION is False
        assert tpb.CONTROL_PLANE_REPAIR_STRATEGY_OPINION is False

    def test_generic_child_roles_exclude_task_main(self):
        assert tpb.TASK_MAIN_LLM_CALLER_ROLE == "task-main"
        assert tpb.GENERIC_CHILD_TASK_ROLES == (
            "analyst",
            "coder",
            "reviewer",
            "project-steward",
        )
        assert tpb.TASK_MAIN_LLM_CALLER_ROLE not in tpb.GENERIC_CHILD_TASK_ROLES

    def test_consistent_with_agent_facing_contract(self):
        assert afc.CONTROL_PLANE_IS_WORKFLOW_BRAIN is False
        assert afc.CONTROL_PLANE_IS_SEMANTIC_INTERPRETER is False
        assert afc.CONTROL_PLANE_REPLACES_LLM_REASONING is False


# ---------------------------------------------------------------------------
# AC2 — lifecycle/state separation
# ---------------------------------------------------------------------------


class TestLifecycleStateSeparation:
    def test_execution_lifecycle_state_is_not_project_workflow_state(self):
        assert tpb.EXECUTION_LIFECYCLE_STATE_NE_PROJECT_WORKFLOW_STATE is True

    def test_control_plane_does_not_validate_workflow_position(self):
        assert tpb.CP_DOES_NOT_VALIDATE_PLAN_WORKFLOW_POSITION is True
        assert tpb.CP_DOES_NOT_ASK_WHY_TASK_IS_STARTED is True

    def test_forbidden_workflow_validations_declared(self):
        forbidden = set(tpb.TASK_START_CONTROL_PLANE_MUST_NOT_VALIDATE)
        assert {
            "plan_workflow_position",
            "work_item_readiness",
            "previous_work_item_completion",
            "plan_dag_position",
            "review_timing",
            "review_frequency",
            "reviewer_choice_reason",
            "repair_strategy",
            "milestone_closure_semantics",
            "next_milestone_progression_strategy",
        } <= forbidden

    def test_allowed_and_forbidden_validations_disjoint(self):
        assert not set(tpb.TASK_START_CONTROL_PLANE_VALIDATES) & set(
            tpb.TASK_START_CONTROL_PLANE_MUST_NOT_VALIDATE
        )


# ---------------------------------------------------------------------------
# AC3 — task.start semantic surface
# ---------------------------------------------------------------------------


class TestTaskStartSemanticSurface:
    def test_llm_supplies_only_role_and_handoff_ref(self):
        assert tpb.TASK_START_OPERATION == "task.start"
        assert tpb.TASK_START_LLM_SUPPLIED == ("role", "handoff_ref")

    def test_canonical_descriptor_matches_contract(self):
        from aota_forge.work_plane.task_main_descriptors import TASK_START_DESCRIPTOR

        assert tuple(spec.name for spec in TASK_START_DESCRIPTOR.inputs) == (
            tpb.TASK_START_LLM_SUPPLIED
        )

    def test_no_model_supplied_authority_identity(self):
        for name in tpb.TASK_START_LLM_SUPPLIED:
            lowered = name.lower()
            for forbidden in (
                "project",
                "worktree",
                "session",
                "authority",
                "task_id",
                "timeout",
                "store",
            ):
                assert forbidden not in lowered

    def test_facade_exposes_role_and_handoff_ref(self):
        params = inspect.signature(task_start).parameters
        assert "role" in params
        assert "handoff_ref" in params
        assert params["role"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["handoff_ref"].kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# AC4 — trusted enrichment boundary
# ---------------------------------------------------------------------------


class TestTrustedEnrichmentBoundary:
    def test_control_plane_enriches_trusted_runtime_information(self):
        enriched = set(tpb.TASK_START_CONTROL_PLANE_ENRICHES)
        assert {
            "project_binding",
            "project_root",
            "worktree_root",
            "worktree_boundary",
            "canonical_task_id",
            "parent_task_identity",
            "origin_session_identity",
            "runtime_profile",
            "tool_surface",
            "role_authority",
            "execution_store",
            "completion_route",
            "handoff_provenance_digest",
            "dispatch_idempotency",
        } <= enriched

    def test_mechanical_validation_declared(self):
        validated = set(tpb.TASK_START_CONTROL_PLANE_VALIDATES)
        assert {
            "caller_authority",
            "trusted_project_binding",
            "project_boundary",
            "handoff_existence_integrity",
            "worktree_containment",
            "role_authority",
            "tool_authority",
            "resource_bounds",
            "schema_input_validity",
        } <= validated

    def test_model_authored_binding_not_trusted(self):
        assert tpb.MODEL_AUTHORED_TRUSTED_RUNTIME_BINDING is False
        assert afc.SERVER_SIDE_AUTHORITY_REQUIRED is True

    def test_enrichment_disjoint_from_model_supplied(self):
        assert not set(tpb.TASK_START_CONTROL_PLANE_ENRICHES) & set(
            tpb.TASK_START_LLM_SUPPLIED
        )
        assert not set(tpb.TASK_START_CONTROL_PLANE_VALIDATES) & set(
            tpb.TASK_START_LLM_SUPPLIED
        )


# ---------------------------------------------------------------------------
# AC5 — handoff cannot grant authority
# ---------------------------------------------------------------------------


class TestHandoffBoundary:
    def test_handoff_is_semantic_artifact_not_authority(self):
        assert tpb.HANDOFF_SEMANTIC_ARTIFACT is True
        assert tpb.HANDOFF_IS_AUTHORITY is False

    def test_semantic_content_shape(self):
        assert tpb.HANDOFF_SEMANTIC_CONTENT == (
            "objective",
            "bounded_scope",
            "validation_expectations",
            "semantic_stop_expectations",
        )

    def test_handoff_cannot_elevate_authority(self):
        assert {
            "project_authority",
            "write_authority",
            "tool_authority",
            "role_authority",
            "worktree_authority",
        } == set(tpb.HANDOFF_CANNOT_ELEVATE)

    def test_consistent_with_existing_handoff_contract(self):
        assert afc.HANDOFF_DISPATCHES_WORKER is False
        assert afc.HANDOFF_TERMINATES_TASK is False


# ---------------------------------------------------------------------------
# AC6 — reviewer genericity
# ---------------------------------------------------------------------------


class TestReviewerGenericity:
    def test_reviewer_has_no_special_workflow_state(self):
        assert tpb.REVIEWER_ROLE == "reviewer"
        assert tpb.REVIEWER_SPECIAL_WORKFLOW_STATE_REQUIRED is False
        assert tpb.REVIEWER_USES_GENERIC_CHILD_TASK_LIFECYCLE is True

    def test_reviewer_is_a_generic_child_role(self):
        assert "reviewer" in tpb.GENERIC_CHILD_TASK_ROLES
        assert "reviewer" in afc.ONE_SHOT_ROLES

    def test_reviewer_write_authority_stays_role_policy(self):
        assert afc.REVIEWER_PRODUCT_SOURCE_WRITE_ALLOWED is False


# ---------------------------------------------------------------------------
# AC7 — legacy freeze + reverse-coupling classification
# ---------------------------------------------------------------------------


class TestLegacyFreeze:
    def test_freeze_flags(self):
        assert tpb.LEGACY_WORKFLOW_PATH_FROZEN is True
        assert tpb.LEGACY_REMOVAL_INITIAL_PLAN is False
        assert tpb.NO_FURTHER_LEGACY_SEMANTIC_EXPANSION is True

    def test_frozen_files_present(self):
        assert tpb.frozen_legacy_files_present() is True
        for path in tpb.frozen_legacy_file_paths():
            assert path.is_file(), f"frozen legacy file missing: {path}"

    def test_frozen_machinery_declared(self):
        machinery = set(tpb.LEGACY_FROZEN_MACHINERY)
        assert {
            "task_main.activate_milestone",
            "task_main.advance_once",
            "task_main.submit_work_projection",
            "MilestonePlanView",
            "READY work calculation",
            "progression-complete",
            "INTEGRATED_REVIEW_REQUIRED",
            "DISPATCHED_REVIEW",
            "REPAIR_REQUIRED",
            "MILESTONE_CLOSURE_READY",
            "NEXT_MILESTONE_USER_GATE",
        } <= machinery

    @pytest.mark.parametrize("symbol", tpb.LEGACY_FROZEN_SYMBOLS)
    def test_frozen_symbols_still_importable(self, symbol: str):
        assert _resolve_dotted(symbol) is not None


class TestReverseCouplingMap:
    def test_entries_classified_as_legacy(self):
        assert tpb.REVERSE_COUPLING_MAP
        for entry in tpb.REVERSE_COUPLING_MAP:
            assert entry.classification == tpb.LEGACY_COMPATIBILITY_COUPLING
            assert entry.required_for_thin_path is False

    def test_importers_and_imported_modules_exist(self):
        for entry in tpb.REVERSE_COUPLING_MAP:
            assert _module_file_exists(entry.importer), entry.importer
            for imported in entry.imported:
                assert _module_file_exists(imported), imported

    def test_known_couplings_present(self):
        by_importer = {entry.importer: entry for entry in tpb.REVERSE_COUPLING_MAP}
        binding = by_importer["aota_forge.runtime.trusted_runtime_binding"]
        assert "aota_forge.runtime.task_main.control" in binding.imported
        assert "aota_forge.runtime.task_main.coordinator" in binding.imported
        assert "TaskMainControlService" in binding.symbols
        assert "MilestonePlanView" in binding.symbols

        bootstrap = by_importer["aota_forge.composition.task_main_host_bootstrap"]
        assert "aota_forge.runtime.task_main.coordinator" in bootstrap.imported

        ingress = by_importer["aota_forge.core_ingress"]
        assert "aota_forge.runtime.task_main.control" in ingress.imported

    def test_legacy_advance_once_still_registered_on_canonical_dispatch(self):
        from aota_forge.core_ingress import PROVIDER_BACKED_OPERATIONS

        assert "task_main.advance_once" in PROVIDER_BACKED_OPERATIONS


# ---------------------------------------------------------------------------
# AC8 — foundation reuse / adapt boundary
# ---------------------------------------------------------------------------


class TestFoundationReuseBoundary:
    def test_dispositions_valid_and_unique(self):
        assert set(tpb.FOUNDATION_DISPOSITIONS) == {
            tpb.REUSE_AS_IS,
            tpb.ADAPT_FOR_THIN_PATH,
        }
        names = [entry.foundation for entry in tpb.FOUNDATION_BOUNDARY]
        assert len(names) == len(set(names))
        for entry in tpb.FOUNDATION_BOUNDARY:
            assert entry.disposition in tpb.FOUNDATION_DISPOSITIONS
            assert entry.owners

    def test_owner_paths_exist(self):
        for entry in tpb.FOUNDATION_BOUNDARY:
            for owner in entry.owners:
                assert (REPO_ROOT / owner).exists(), owner

    def test_key_foundations_reused_as_is(self):
        reuse = {
            entry.foundation: entry
            for entry in tpb.FOUNDATION_BOUNDARY
            if entry.disposition == tpb.REUSE_AS_IS
        }
        assert "aota_forge/work_plane/handoff.py" in reuse["task_handoff_store"].owners
        assert "aota_forge/core/execution/results.py" in reuse[
            "canonical_result_and_result_governance"
        ].owners
        assert "aota_forge/work_plane/result_card.py" in reuse["worker_result_card"].owners
        assert "aota_forge/work_plane/workspace_tools.py" in reuse[
            "workspace_search_read_write"
        ].owners
        assert "aota_forge/mcp_transport.py" in reuse["aota_invoke_transport"].owners
        assert "aota_forge/work_plane/task_facade.py" in reuse[
            "task_start_task_return_facade"
        ].owners

    def test_known_adapt_seams_declared(self):
        adapt_owners = {
            owner
            for entry in tpb.FOUNDATION_BOUNDARY
            if entry.disposition == tpb.ADAPT_FOR_THIN_PATH
            for owner in entry.owners
        }
        assert {
            "aota_forge/core_ingress/__init__.py",
            "aota_forge/mcp_transport.py",
            "aota_forge/composition/task_main_host_bootstrap.py",
            "aota_forge/runtime/trusted_runtime_binding.py",
            "aota_forge/work_plane/task_facade.py",
        } <= adapt_owners

    def test_no_new_foundation_duplication_or_engine(self):
        assert tpb.FOUNDATION_DUPLICATION_INTRODUCED is False
        assert tpb.NEW_EXECUTION_ENGINE_CREATED is False
        assert tpb.NEW_AUTHORITY_ENGINE_CREATED is False
        assert tpb.NEW_RESULT_ONTOLOGY_CREATED is False
        assert tpb.NEW_WORKFLOW_ENGINE_CREATED is False
        assert afc.NEW_EXECUTION_ENGINE_CREATED is False
        assert afc.NEW_AUTHORITY_ENGINE_CREATED is False
        assert afc.NEW_RESULT_ONTOLOGY_CREATED is False
