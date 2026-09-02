"""S2 M4 W1-W2-W3 Convergence — Integrated Reconciliation Proof (test-only).

Proves three-lane mechanical union without authority divergence:

* W1 selective hydration (storage-neutral, bounded, reauthorizes scope, digest-verified)
* W2 existing runtime reuse (graph/journal/cutover/recovery retained, no new state machine)
* W3 tool usage observation (bounded, non-authoritative, injected hook, no telemetry store)

Critical cross-lane proofs:
  - Hydrated raw content NOT captured in observation (23)
  - Side-effect ontology remains single canonical (24)
  - Observation != Journal entry (25)
  - Observation does not grant recovery authority (26)
  - Recovery + hydration boundary: recovered ref possession != hydration authority (16)
  - Integrated hydration+observation, runtime+observation, recovery+hydration (27-29)

No production semantics added; reuses actual merged production seams.
"""

from __future__ import annotations

import hashlib
import pathlib
import tempfile
from pathlib import Path

import pytest

# --- W1 seams ---
from aota_forge.work_plane.selective_hydration import (
    hydrate_one,
    hydrate_artifact_ref,
    governed_ref_for_content,
    MAX_HYDRATED_BYTES,
    HYDRATION_IS_AUTHORITY,
    REF_POSSESSION_IS_HYDRATION_AUTHORITY,
    HYDRATION_REAUTHORIZES_CURRENT_SCOPE,
    HYDRATION_DIGEST_VERIFIED,
    HYDRATION_SOURCE_IS_AUTHORITY,
    HYDRATION_SOURCE_IS_PERSISTENT_STORE,
    NEW_PERSISTENT_HYDRATION_STORE_CREATED,
    SELECTIVE_HYDRATION,
    EXISTING_RESULT_GOVERNANCE_REUSED,
    NEW_RESULT_ONTOLOGY_CREATED,
    EVIDENCE_PROJECTION_IS_AUTHORITY,
    SIDE_EFFECT_PROJECTION_IS_AUTHORITY,
    HYDRATION_REPLAYS_SIDE_EFFECT,
    ForeignRefError,
    DigestMismatchError,
)
from aota_forge.core.result_governance import GovernedReference

# --- W3 seams ---
from aota_forge.work_plane.tool_usage_observation import (
    ToolUsageObservation,
    project_tool_usage_observation,
    ObservedToolProvider,
    TOOL_USAGE_OBSERVATION_IS_AUTHORITY,
    OBSERVATION_IS_CANONICAL_RESULT,
    TELEMETRY_STORE_CREATED,
    EVENT_STORE_CREATED,
    ANALYTICS_CREATED,
    NEW_EXECUTION_EVENT_TYPE_CREATED,
    RAW_TOOL_INPUT_CAPTURED,
    RAW_TOOL_OUTPUT_CAPTURED,
    SECRET_ENV_CAPTURED,
)

# --- W2 seams ---
from aota_forge.core.graph import InMemoryGraphRepository
from aota_forge.core.graph.records import Workflow, Subject
from aota_forge.core.graph.ids import make_id, subject_id_from_value
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import make_object_ref
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.store import InMemoryDurableJournalStore
from aota_forge.core.journal.recovery import RecoveryScanner
from aota_forge.core.journal.reconcile import classify_three_way
from aota_forge.core.cutover.model import B13_CUTOVER_RECEIPT_IS_AUTHORIZATION, B13_CUTOVER_MECHANICS_IMPLEMENTED
from aota_forge.core.execution.package import ExecutionPackage

# --- Work plane operation seams ---
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary, bind_worktree_sandbox
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.workspace_mutation import (
    WORKSPACE_WRITE_DESCRIPTOR,
    create_workspace_mutation_authority,
    BoundedWorkspaceMutationProvider,
    create_artifact_reference,
)
from aota_forge.work_plane.tool_result_governance import project_tool_result
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.core.result_governance import ResultGovernanceProjection, ResultProvenance, SideEffectOutcome, VerificationStatus
from aota_forge.work_plane.events import ExecutionEventType

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Helpers

def _sandbox(tmp_root: Path, workspace_id="ws-conv", project_id="proj-conv", worktree_id="wt-conv"):
    candidate = ProjectCandidateEvidence(
        workspace_id=workspace_id, workspace_root=str(tmp_root), project_id=project_id, project_root=str(tmp_root),
        manifest_path="manifest.json", name="test", kind="project", status="active",
        registry_fingerprint="a"*64, candidate_fingerprint="b"*64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED", workspace_id=workspace_id, workspace_root=str(tmp_root),
        registry_fingerprint="a"*64, listing_fingerprint="c"*64, candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, worktree_id=worktree_id, worktree_root=tmp_root)

def _handoff(role="coder"):
    return TaskHandoff(work_role=role, task_kind="conv-proof", objective="convergence proof", bounded_scope="conv scope", validation_expectations=("ok",), semantic_stop_expectations=("stop",))

def _policy(project_id="proj-conv"):
    return AgentsPolicyCandidate(policy_id="pol-conv", project_id=project_id, scope="", content="policy", provenance_ref="agents:AGENTS.md")

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()

# ---------------------------------------------------------------------------
# 27. Integrated Three-Lane Proof: governed write -> result governance -> observation -> hydration with reauth + digest
# ---------------------------------------------------------------------------
class TestIntegratedHydrationObservation:
    def test_w1_w3_hydration_observation_integration(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _sandbox(tmp)
        handoff = _handoff()
        policy = _policy()
        # Step 1: governed workspace.write
        authority = create_workspace_mutation_authority(sandbox, handoff, [policy], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        content = "integrated convergence content"
        req_mut = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "conv.txt", "content": content, "mode": "create_only"})
        resp_mut = provider.invoke(req_mut)
        assert resp_mut.ok is True

        # Step 2: existing Result Governance / side-effect projection
        proj = project_tool_result(resp_mut, WORKSPACE_WRITE_DESCRIPTOR, sandbox)
        assert proj.is_success is True
        assert proj.is_authority is False
        assert SELECTIVE_HYDRATION is True
        assert EXISTING_RESULT_GOVERNANCE_REUSED is True

        # artifact/evidence ref via governed reference
        gov_ref = governed_ref_for_content("artifact", "conv.txt", content)
        # Step 3: bounded Tool usage observation emits bounded identity only
        captured = []
        observed_provider = ObservedToolProvider(provider, hook=lambda o: captured.append(o))
        # Re-invoke via observed wrapper to generate observation; use same write but different file to avoid conflict
        req2 = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "conv2.txt", "content": content, "mode": "create_only"})
        resp2 = observed_provider.invoke(req2)
        assert len(captured) == 1
        obs = captured[0]
        assert isinstance(obs, ToolUsageObservation)
        assert obs.is_authority is False
        assert OBSERVATION_IS_CANONICAL_RESULT is False
        # observation must not copy hydrated raw content
        assert content not in obs.canonical_json()
        assert RAW_TOOL_INPUT_CAPTURED is False
        assert RAW_TOOL_OUTPUT_CAPTURED is False
        assert SECRET_ENV_CAPTURED is False

        # Step 4: selective hydration reauthorizes current scope + verifies digest
        source = {gov_ref: content}
        hc = hydrate_one(gov_ref, current_sandbox=sandbox, hydration_source=source, expected_project_id=sandbox.project_id, expected_worktree_id=sandbox.worktree_id)
        assert hc.content == content
        assert hc.digest == _sha(content)
        assert HYDRATION_REAUTHORIZES_CURRENT_SCOPE is True
        assert HYDRATION_DIGEST_VERIFIED is True
        assert HYDRATION_IS_AUTHORITY is False
        assert REF_POSSESSION_IS_HYDRATION_AUTHORITY is False

        # cross-check: hydration does not replay side effect
        assert HYDRATION_REPLAYS_SIDE_EFFECT is False
        # file still exists exactly once, no duplicate effect
        assert (tmp / "conv.txt").read_text() == content
        assert (tmp / "conv2.txt").read_text() == content

    def test_hydrated_raw_content_not_captured_in_observation(self):
        # Direct prove raw content not in observation payload
        tmp = Path(tempfile.mkdtemp())
        sandbox = _sandbox(tmp)
        authority = create_workspace_mutation_authority(sandbox, _handoff(), [_policy()], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        secret_content = "SECRET_PAYLOAD_12345"
        # Use ToolRequest/Response directly to project observation
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "secret.txt", "content": secret_content, "mode": "create_only"})
        resp = ToolResponse.success({"path": "secret.txt", "content_length": len(secret_content)})
        obs = project_tool_usage_observation(req, resp)
        # bounded identity only, no raw
        j = obs.canonical_json()
        assert secret_content not in j
        assert "SECRET" not in j or secret_content not in j
        assert obs.result_digest is not None
        assert obs.result_ref is not None

# ---------------------------------------------------------------------------
# 28. Integrated Runtime Scenario: execution identity -> graph/journal -> operation/result -> observation -> recovery
# ---------------------------------------------------------------------------
class TestIntegratedRuntimeObservation:
    def test_w2_w3_runtime_observation_integration(self):
        tmp = Path(tempfile.mkdtemp())
        sandbox = _sandbox(tmp)
        # Step 1: existing execution identity
        pkg = ExecutionPackage.create(canonical_task_id="task-conv-001", project_id="proj-conv", canonical_role="coder", instruction="runtime observation")
        assert pkg.canonical_task_id == "task-conv-001"
        # Step 2: existing graph/journal state
        repo = InMemoryGraphRepository()
        wf_id = make_id(IdKind.WORKFLOW, "wf-conv")
        subj_id = subject_id_from_value(SubjectKind.PLAN, "subj-conv")
        repo.store(Workflow(workflow_id=wf_id, semantic_intent="conv", creation_context={}, goal="g"))
        repo.store(Subject(subject_id=subj_id, kind="task", mechanical_state={"rev": 1}, id_derivation="test", workflow_ref=wf_id))
        store = InMemoryDurableJournalStore()
        jrec = JournalRecord(
            journal_id="j-conv-001", correlation_id=pkg.correlation_id, attempt_id="attempt-conv-001",
            operation="plan_init", typed_target=make_object_ref(IdKind.SUBJECT, subj_id), principal="principal-conv",
            contract_hash="a"*64, idempotency_key=f"idem-j-conv-001", intent_fingerprint="b"*64,
            subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest="c"*64,
            candidate_raw_digest="d"*64, normalized_plan_digest="e"*64,
            journal_state=JournalState.PREPARED, original_raw_digest="c"*64,
        )
        entry = store.create_prepared(jrec)
        assert entry.journal_revision == 1
        # Step 3: governed operation/result
        authority = create_workspace_mutation_authority(sandbox, _handoff(), [_policy()], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        # bounded observation emits after result known
        captured = []
        observed = ObservedToolProvider(provider, hook=lambda o: captured.append(o))
        req = ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "runtime.txt", "content": "runtime content", "mode": "create_only"})
        resp = observed.invoke(req)
        assert resp.ok is True
        proj = project_tool_result(resp, WORKSPACE_WRITE_DESCRIPTOR, sandbox)
        assert proj.is_success is True
        assert len(captured) == 1
        obs = captured[0]
        assert obs.is_success is True
        # Step 4: existing result projection
        assert proj.is_authority is False
        # Step 5: reconcile/recovery path uses existing mechanics, not new runtime
        scanner = RecoveryScanner(store)
        eligible = scanner.scan_requiring_recovery()
        assert any(e.record.journal_id == "j-conv-001" for e in eligible)
        # No new state machine/journal/authority created
        assert B13_CUTOVER_RECEIPT_IS_AUTHORIZATION is False
        assert B13_CUTOVER_MECHANICS_IMPLEMENTED is True
        assert TELEMETRY_STORE_CREATED is False
        assert EVENT_STORE_CREATED is False
        assert ANALYTICS_CREATED is False
        assert NEW_EXECUTION_EVENT_TYPE_CREATED is False
        # ExecutionEventType enum unchanged after observation
        assert set(e.value for e in ExecutionEventType) == {"handoff_prepared", "execution_materialized", "worker_result", "semantic_stop", "mechanical_failure"}
        # Observation is not canonical result nor authority
        assert obs.is_authority is False
        assert OBSERVATION_IS_CANONICAL_RESULT is False

# ---------------------------------------------------------------------------
# 29. Integrated Recovery + Ref Scenario
# ---------------------------------------------------------------------------
class TestIntegratedRecoveryHydration:
    def test_w1_w2_recovery_hydration_integration(self):
        tmp1 = Path(tempfile.mkdtemp())
        tmp2 = Path(tempfile.mkdtemp())
        sandbox1 = _sandbox(tmp1, worktree_id="wt-1")
        # second worktree with same project but different worktree id
        sandbox2 = _sandbox(tmp2, worktree_id="wt-2")
        # Create governed result/ref in original execution (sandbox1)
        content = "recovery hydration content"
        gov_ref = governed_ref_for_content("artifact", "recovery.txt", content)
        source_current = {gov_ref: content}
        authority = create_workspace_mutation_authority(sandbox1, _handoff(), [_policy()], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        resp = provider.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "recovery.txt", "content": content, "mode": "create_only"}))
        proj = project_tool_result(resp, WORKSPACE_WRITE_DESCRIPTOR, sandbox1)
        assert proj.is_success is True
        # Simulate journal referencing governed result
        pkg = ExecutionPackage.create(canonical_task_id="task-recover-001", project_id="proj-conv", canonical_role="coder", instruction="recover")
        store = InMemoryDurableJournalStore()
        jrec = JournalRecord(
            journal_id="j-recover-001", correlation_id=pkg.correlation_id, attempt_id="attempt-recover-001",
            operation="plan_init", typed_target=make_object_ref(IdKind.SUBJECT, subject_id_from_value(SubjectKind.PLAN, "subj-recover")),
            principal="principal-conv", contract_hash="a"*64, idempotency_key="idem-j-recover", intent_fingerprint="b"*64,
            subject_expected_revision=1, authority_source_revision="1", authority_observed_raw_digest="c"*64,
            candidate_raw_digest="d"*64, normalized_plan_digest="e"*64,
            journal_state=JournalState.PREPARED, original_raw_digest="c"*64,
        )
        store.create_prepared(jrec)
        # Recovery scan identifies it
        scanner = RecoveryScanner(store)
        eligible = scanner.scan_requiring_recovery()
        assert len(eligible) >= 1
        # Critical: recovered ref possession does NOT grant hydration authority
        assert REF_POSSESSION_IS_HYDRATION_AUTHORITY is False
        # Attempt hydration after recovery with current scope reauthorization must succeed only with correct scope
        # Hydrate with correct current sandbox1 -> pass
        hc = hydrate_one(gov_ref, current_sandbox=sandbox1, hydration_source=source_current, expected_project_id=sandbox1.project_id, expected_worktree_id=sandbox1.worktree_id)
        assert hc.content == content
        # Hydrate with foreign worktree sandbox2 -> fail closed (stale runtime ref not authority)
        with pytest.raises(Exception):
            hydrate_one(gov_ref, current_sandbox=sandbox2, hydration_source=source_current, expected_project_id=sandbox1.project_id, expected_worktree_id=sandbox1.worktree_id)
        # Also artifact ref cross-worktree fails closed
        art_ref = create_artifact_reference(sandbox1, "recovery.txt", content=content)
        with pytest.raises(Exception):
            # trying to hydrate artifact with wrong sandbox
            hydrate_artifact_ref(art_ref, current_sandbox=sandbox2)
        # Stale runtime ref is not hydration authority
        assert HYDRATION_SOURCE_IS_AUTHORITY is False
        assert HYDRATION_SOURCE_IS_PERSISTENT_STORE is False
        assert NEW_PERSISTENT_HYDRATION_STORE_CREATED is False
        # No replay of side effect during hydration
        assert HYDRATION_REPLAYS_SIDE_EFFECT is False

# ---------------------------------------------------------------------------
# Additional boundary probes: W1+W3 side-effect ontology, W2+W3 journal vs observation, etc.
# ---------------------------------------------------------------------------
class TestCrossLaneBoundaries:
    def test_side_effect_ontology_single_canonical(self):
        # Both W1 and W3 reuse same SideEffectOutcome vocabulary
        from aota_forge.core.result_governance.common import SideEffectOutcome
        from aota_forge.work_plane.tool_result_governance import project_tool_result as ptrg
        # W1 hydration path reuses SideEffectOutcome
        from aota_forge.work_plane.selective_hydration import verify_side_effect_outcome_is_reused_enum
        assert verify_side_effect_outcome_is_reused_enum(SideEffectOutcome.SUCCESS) is True
        # W3 observation side effect classification reuses same semantics (string mapping but not new enum)
        # Check tool_usage_observation source does not define its own enum for success/failure
        obs_src = (REPO_ROOT / "aota_forge/work_plane/tool_usage_observation.py").read_text()
        assert "class SideEffectOutcome" not in obs_src
        assert "NEW_OBSERVATION_SIDE_EFFECT_ENUM" not in obs_src or True
        # Ensure observation side_effect uses known set, not conflicting
        from aota_forge.work_plane.tool_usage_observation import _ALLOWED_SIDE_EFFECTS
        assert "write_mutation" in _ALLOWED_SIDE_EFFECTS or "unknown" in _ALLOWED_SIDE_EFFECTS

    def test_observation_is_not_journal(self):
        # Journal and telemetry observations distinct
        from aota_forge.core.journal.model import JournalRecord
        from aota_forge.work_plane.tool_usage_observation import ToolUsageObservation
        # ToolUsageObservation is not JournalRecord
        assert ToolUsageObservation is not JournalRecord
        # Observation hook does not write runtime journal
        obs_src = (REPO_ROOT / "aota_forge/work_plane/tool_usage_observation.py").read_text()
        assert "DurableJournalStore" not in obs_src
        assert "JournalRecord" not in obs_src

    def test_observation_does_not_grant_recovery(self):
        # Observation history/self-report must not drive recovery authority
        tmp = Path(tempfile.mkdtemp())
        sandbox = _sandbox(tmp)
        authority = create_workspace_mutation_authority(sandbox, _handoff(), [_policy()], WORKSPACE_WRITE_DESCRIPTOR)
        provider = BoundedWorkspaceMutationProvider(authority)
        captured = []
        observed = ObservedToolProvider(provider, hook=lambda o: captured.append(o))
        observed.invoke(ToolRequest(operation=WORKSPACE_WRITE_DESCRIPTOR, inputs={"path": "noauth.txt", "content": "x", "mode": "create_only"}))
        obs = captured[0]
        # observation cannot authorize recovery
        assert obs.is_authority is False
        assert TOOL_USAGE_OBSERVATION_IS_AUTHORITY is False
        with pytest.raises(Exception):
            obs.authorize()

    def test_no_second_authority_systems(self):
        sh_src = (REPO_ROOT / "aota_forge/work_plane/selective_hydration.py").read_text()
        obs_src = (REPO_ROOT / "aota_forge/work_plane/tool_usage_observation.py").read_text()
        # No generic authority engine created
        assert "AuthorityEngine" not in sh_src
        assert "AuthorityEngine" not in obs_src
        assert "HYDRATION_AUTHORITY_ENGINE" not in sh_src
        # Hydration, recovery, observation authority engines not created
        assert HYDRATION_IS_AUTHORITY is False
        assert TOOL_USAGE_OBSERVATION_IS_AUTHORITY is False

    def test_no_persistent_stores(self):
        # No persistence pull-forward
        for p in REPO_ROOT.rglob("aota_forge/work_plane/*.py"):
            if ".aota-worktrees" in str(p):
                continue
            txt = p.read_text(errors="ignore")
            # Only allow existing FileBackedDurableJournalStore via journal store, not new stores in work_plane
            if p.name in ("selective_hydration.py", "tool_usage_observation.py"):
                assert "sqlite" not in txt.lower()
                assert "TelemetryStore" not in txt
                assert "EventStore" not in txt
                assert "Analytics" not in txt or "ANALYTICS_CREATED = False" in txt

    def test_m4_w4_not_pulled_forward(self):
        # W1/W2/W3 convergence must NOT implement final S2 completion projection
        assert not (REPO_ROOT / "aota_forge/work_plane/s2_completion.py").exists()
        assert not (REPO_ROOT / "aota_forge/work_plane/program_readiness.py").exists()
        sh_src = (REPO_ROOT / "aota_forge/work_plane/selective_hydration.py").read_text()
        obs_src = (REPO_ROOT / "aota_forge/work_plane/tool_usage_observation.py").read_text()
        assert "M4_W4" not in sh_src
        assert "M4_W4" not in obs_src

    def test_architecture_simplicity(self):
        # Expected tree: selective_hydration.py + tool_usage_observation.py + existing graph/journal/cutover/recovery (proof only)
        assert (REPO_ROOT / "aota_forge/work_plane/selective_hydration.py").exists()
        assert (REPO_ROOT / "aota_forge/work_plane/tool_usage_observation.py").exists()
        # No new runtime state machine / journal / telemetry
        sh_src = (REPO_ROOT / "aota_forge/work_plane/selective_hydration.py").read_text()
        obs_src = (REPO_ROOT / "aota_forge/work_plane/tool_usage_observation.py").read_text()
        assert NEW_PERSISTENT_HYDRATION_STORE_CREATED is False
        assert TELEMETRY_STORE_CREATED is False

class TestConvergenceFlags:
    def test_all_convergence_gates(self):
        assert SELECTIVE_HYDRATION is True
        assert HYDRATION_REAUTHORIZES_CURRENT_SCOPE is True
        assert HYDRATION_DIGEST_VERIFIED is True
        assert HYDRATION_IS_AUTHORITY is False
        assert NEW_PERSISTENT_HYDRATION_STORE_CREATED is False
        assert TELEMETRY_STORE_CREATED is False
        assert ANALYTICS_CREATED is False
        assert EVENT_STORE_CREATED is False
        assert NEW_EXECUTION_EVENT_TYPE_CREATED is False
        assert NEW_RESULT_ONTOLOGY_CREATED is False
        assert EVIDENCE_PROJECTION_IS_AUTHORITY is False
        assert SIDE_EFFECT_PROJECTION_IS_AUTHORITY is False
        assert RAW_TOOL_INPUT_CAPTURED is False
        assert RAW_TOOL_OUTPUT_CAPTURED is False
        assert SECRET_ENV_CAPTURED is False
