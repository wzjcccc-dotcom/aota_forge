"""AF #54 M2/W4 — duplicate guidance reduction + model-visible contract proof.

Channel-responsibility proof (semantic markers, not prose snapshots):

  G1  the detailed work_item handoff/task contract has ONE canonical
      procedural owner (task-main Role Skill body); eager guidance,
      OPERATION_GUIDANCE, Worker startup and SOULs stay minimal
  G2  discovery survives reduction: the thin eager guidance still names the
      owner Skill and both first-use invariants
  G3  SOUL stays role identity (no tool manuals: no payload JSON, no write
      mode enums, no argument shapes)
  G4  Worker startup prompt stays minimal (lifecycle order line only)
  G5  B002 regression: handoff.write(work_role=reviewer) ->
      task.start(role=reviewer) normal path is valid first-attempt with
      zero ROLE_HANDOFF_MISMATCH
  G6  negative probes: N1 missing work identity fails closed; N2 role !=
      handoff role fails closed with zero child execution; N3 reviewer write
      denied; N4 Skill prose never changes the server-side surface
  G7  tool schema stays shared+thin (operations.yaml surface unchanged by
      M2; no second handoff schema; CONTROL_PLANE_IS_ROLE_USAGE_TUTOR=no)

PROVES=model-visible guidance channel convergence at the real ingress
       seams with explicit test doubles.
DOES_NOT_PROVE=real task-main LLM first-attempt quality (bounded V3 smoke
       recorded separately in M2/W4 evidence) and effectiveness metrics (M4).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.work_plane import af_roles
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.task_facade import ROLE_HANDOFF_MISMATCH_CODE
from aota_forge.work_plane.task_main_descriptors import (
    build_task_main_operation_guidance,
    build_thin_task_main_operation_guidance,
)
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "proj-af54m2w4"
WORKTREE_ID = "wt-af54m2w4"

# markers that only belong in the canonical detailed Skill, never in thin
# affordance channels
# per-code recovery rows / identity lectures belong ONLY in the canonical
# detailed Skill body; naming the owner (e.g. "typed-error recovery lives
# in ...") is the allowed discovery pointer.
_TUTORIAL_MARKERS = (
    "WORK_SCOPE_INSUFFICIENT  ->",
    "DIGEST_MISMATCH",
    "execution order",
    "new W number",
)


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    yield
    reset_execution_dispatcher()


def _skill_body() -> str:
    return (ROOT / "skills" / "aota-task-main-control" / "SKILL.md").read_text(encoding="utf-8")


def _sandbox(root: Path) -> WorktreeSandboxBoundary:
    root.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws-af54m2w4",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        worktree_id=WORKTREE_ID,
        worktree_root=str(root.resolve()),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


def _task_main_binding(sandbox: WorktreeSandboxBoundary) -> CanonicalDispatchBinding:
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="af54-m2w4",
        objective="thin task-main",
        bounded_scope="thin task-main scope",
        validation_expectations=["focused"],
        semantic_stop_expectations=["stop"],
    )
    return CanonicalDispatchBinding(
        canonical_task_id="main-af54m2w4",
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=create_role_tool_surface(
            "task-main",
            eager=("handoff.write", "handoff.open", "task.start"),
            progressive=(),
        ),
        trusted_task_main_context=None,
    )


class _RecordingAdapter(ReferenceFakeExecutorAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.packages: list = []

    def dispatch(self, package):
        self.packages.append(package)
        return super().dispatch(package)


def _dispatcher() -> tuple[ExecutionDispatcher, _RecordingAdapter]:
    registry = ExecutorRegistry()
    recording = _RecordingAdapter()
    registry.register(recording)
    return ExecutionDispatcher(registry, state_store=InMemoryExecutionStateStore()), recording


def _write(binding, payload):
    return dispatch_via_core(
        "handoff.write", {"mode": "work_item", "payload": payload}, binding
    )


def _start(binding, ref, role):
    return dispatch_via_core("task.start", {"role": role, "handoff_ref": ref}, binding)


class TestG1CanonicalDetailedOwnerIsTheSkill:
    def test_recovery_tables_only_in_skill_not_in_thin_channels(self) -> None:
        body = _skill_body()
        for marker in _TUTORIAL_MARKERS:
            assert marker in body, marker
            assert marker not in af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE, marker
            assert marker not in str(build_thin_task_main_operation_guidance()), marker
        for role in ("coder", "reviewer"):
            for sid in af_roles._ROLE_SKILL_DEFS[role][0]:
                eager = af_roles.curated_eager_guidance(sid)
                assert not any(m in eager for m in _TUTORIAL_MARKERS), sid

    def test_legacy_guidance_channel_stays_legacy_shaped(self) -> None:
        legacy = build_task_main_operation_guidance()
        assert "task_main.submit_work_projection" in legacy
        thin = build_thin_task_main_operation_guidance()
        assert "task_main.submit_work_projection" not in thin


class TestG2MinimalDiscoverySurvives:
    def test_thin_eager_names_owner_and_first_use_invariants(self) -> None:
        eager = af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE
        assert "aota-task-main-control" in eager
        assert "skill.open" in eager
        assert "payload.work_role" in eager
        assert "work_item_ref" in eager and "milestone_ref" in eager
        assert len(eager) < 1200


class TestG3SoulStaysIdentity:
    @pytest.mark.parametrize(
        "role", ("task-main", "coder", "reviewer", "analyst", "project-steward")
    )
    def test_soul_is_not_a_tool_manual(self, role: str) -> None:
        text = (ROOT / "aota_forge" / "roles" / f"{role}.md").read_text(encoding="utf-8")
        for tool_shape in (
            "create_or_replace",
            '"payload"',
            "work_item_ref",
            "task.start(",
            "handoff.write(",
            "max_results",
            "timeout",
        ):
            assert tool_shape not in text, (role, tool_shape)
        assert len(text) < 2500
        for prefix in ("purpose:", "boundary:", "cannot-do:", "scope discipline:"):
            assert prefix in text

    def test_task_main_soul_no_legacy_operation_verbs(self) -> None:
        text = (ROOT / "aota_forge" / "roles" / "task-main.md").read_text(encoding="utf-8")
        for stale in ("activate", "advance_once", "recover_coordinator", "USER_GATE_REQUIRED"):
            assert re.search(rf"\b{stale}\b", text) is None, stale


class TestG4WorkerStartupMinimal:
    def test_startup_prompt_has_no_procedural_manual(self) -> None:
        text = (
            ROOT / "aota_forge" / "composition" / "worker_startup_prompt.md"
        ).read_text(encoding="utf-8")
        assert len(text) < 2048
        # lifecycle minimum stays; tutorial content does not
        for forbidden in (
            "create_or_replace",
            "work_item_ref",
            "ROLE_HANDOFF_MISMATCH",
            "WORK_SCOPE_INSUFFICIENT",
            "validation_expectations",
        ):
            assert forbidden not in text, forbidden
        for minimum in ("role.bootstrap", "task.return", "bounded_scope"):
            assert minimum in text


class TestG5B002NormalPathRegression:
    def test_reviewer_pair_valid_first_attempt_zero_mismatch(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "g5")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        written = _write(
            binding,
            {
                "work_role": "reviewer",
                "work_item_ref": "W1",
                "milestone_ref": "M2",
                "objective": "Review W1",
                "bounded_scope": "Only W1 files and evidence",
                "validation_expectations": ["focused checks"],
                "semantic_stop_expectations": ["stop on gap"],
            },
        )
        assert written.ok, written.error
        started = _start(binding, written.payload["ref"], "reviewer")
        assert started.ok is True, started.error
        assert ROLE_HANDOFF_MISMATCH_CODE not in str(started.error or "")
        assert len(recording.packages) == 1
        assert f":M2:W1:" in f":{started.payload['task_id']}:"


class TestG6NegativeProbes:
    def test_n1_missing_work_identity_fails_closed_no_dispatch(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "n1")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        written = _write(
            binding,
            {
                "work_role": "reviewer",
                "objective": "Review",
                "bounded_scope": "scope",
            },
        )
        assert written.ok, written.error  # persistence stays semantic-only
        started = _start(binding, written.payload["ref"], "reviewer")
        assert started.ok is False
        assert started.error["code"] == "WORK_SCOPE_INSUFFICIENT"
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []

    def test_n2_role_mismatch_fails_closed_zero_child_execution(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "n2")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        written = _write(
            binding,
            {
                "work_role": "reviewer",
                "work_item_ref": "W2",
                "milestone_ref": "M1",
                "objective": "Review",
                "bounded_scope": "scope",
            },
        )
        assert written.ok, written.error
        started = _start(binding, written.payload["ref"], "coder")
        assert started.ok is False
        assert started.error["code"] == ROLE_HANDOFF_MISMATCH_CODE
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []

    def test_n3_reviewer_workspace_write_denied(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "n3")
        handoff = TaskHandoff(
            work_role="reviewer",
            task_kind="n3",
            objective="review",
            bounded_scope="scope",
            validation_expectations=["v"],
            semantic_stop_expectations=["s"],
        )
        binding = CanonicalDispatchBinding(
            canonical_task_id="reviewer-n3",
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            handoff=handoff,
            sandbox=sandbox,
            tool_surface=get_tool_surface_for_role("reviewer"),
        )
        response = dispatch_via_core(
            "workspace.write",
            {"path": "src/x.py", "content": "x", "mode": "create_or_replace"},
            binding,
        )
        assert response.ok is False
        assert response.error["code"] == "AUTHORITY_DENIED"

    def test_n4_skill_prose_never_moves_the_surface(self) -> None:
        # Skill prose is guidance; the server-side surfaces are constructed
        # from the AF catalog only. Reviewer must not see workspace.write and
        # task-main must not see mutation/test ops regardless of any prose.
        reviewer = get_tool_surface_for_role("reviewer")
        task_main = get_tool_surface_for_role("task-main")
        assert reviewer.is_eager("workspace.write") is False
        assert reviewer.is_progressive("workspace.write") is False
        assert task_main.is_eager("workspace.write") is False
        assert task_main.is_eager("test.run") is False


class TestG7ToolSchemaStaysSharedAndThin:
    def test_operations_yaml_input_shape_unchanged_by_m2(self) -> None:
        from aota_forge.core.contracts.loader import (
            discover_canonical_project_root,
            load_operation_descriptor_map,
        )

        ops = load_operation_descriptor_map(discover_canonical_project_root())
        # thin shared shapes: handoff.write stays {mode, payload}; task.start
        # stays {role, handoff_ref} — M2 must not have invented a second
        # handoff schema or fattened the tool schema with tutorials.
        hw = {s.name: s.type for s in ops["handoff.write"].inputs}
        assert hw == {"mode": "str", "payload": "dict"}
        ts = {s.name: s.type for s in ops["task.start"].inputs}
        assert ts == {"role": "str", "handoff_ref": "str"}

    def test_no_procedural_text_entered_operation_descriptions(self) -> None:
        from aota_forge.core.contracts.loader import (
            discover_canonical_project_root,
            load_operation_descriptor_map,
        )

        ops = load_operation_descriptor_map(discover_canonical_project_root())
        for name in ("handoff.write", "task.start"):
            desc = ops[name].description
            assert "WORK_SCOPE_INSUFFICIENT" not in desc
            assert "re-issue" not in desc.lower()
            assert len(desc) < 800
