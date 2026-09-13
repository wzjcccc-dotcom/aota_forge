"""AF #53 M3/W2-R2 — I53-B002 non-coder role grounding repair (focused test).

Bounded focused proof of the model-contract / mechanical-grounding repair:

  R1  production thin guidance exposes payload.work_role + role equality
  R2  explicit work_role=coder      -> dispatch allowed
  R3  explicit work_role=reviewer   -> dispatch allowed
  R4  explicit work_role=analyst    -> dispatch allowed (generic role freedom)
  R5  thin handoff missing work_role -> typed fail-closed, zero dispatch
  R6  requested reviewer vs grounded coder -> ROLE_HANDOFF_MISMATCH, zero dispatch
  R7  requested coder vs grounded reviewer -> ROLE_HANDOFF_MISMATCH, zero dispatch
  R8  model-visible aota.invoke failure preserves error_code + bounded message
  R9  no raw exception/traceback/path leak in the model-visible failure
  R10 legacy compatibility path preserves the historical coder default
  R11 reviewer child binding gets the canonical reviewer tool surface
  R12 no reviewer-special workflow state is required to start reviewer

PROVES=bounded mechanical contract at the canonical ingress/task facade and
       the real MCP transport projection seam; no new engine/ontology.
DOES_NOT_PROVE=real Hermes task-main LLM behavior or physical Worker spawn
       (covered by the bounded V3 production smoke).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.core.context import bind_trusted_context
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import InMemoryExecutionStateStore
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_via_core
from aota_forge.mcp_transport import create_shared_mcp_server
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedWorkerBinding,
)
from aota_forge.work_plane import thin_path_boundary as tpb
from aota_forge.work_plane.af_roles import get_tool_surface_for_role
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.handoff_store import (
    WORK_ITEM_HANDOFF_ROLE_FIELD,
    WORK_ITEM_HANDOFF_ROLE_REQUIRED_ON_THIN_PATH,
    handoff_write,
)
from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap
from aota_forge.work_plane.task_facade import (
    CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH,
    LEGACY_PATH_MISSING_WORK_ROLE_DEFAULT,
    ROLE_HANDOFF_MISMATCH_CODE,
    THIN_PATH_MISSING_WORK_ROLE_FAILS_CLOSED,
    THIN_WORK_ITEM_ROLE_EXPLICIT,
)
from aota_forge.work_plane.task_main_descriptors import (
    build_thin_task_main_operation_guidance,
)
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "proj-af53m3w2r2"
WORKTREE_ID = "wt-af53m3w2r2"


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    """Canonical ingress binds a process-global dispatcher seam; isolate it."""
    yield
    reset_execution_dispatcher()


# ---------------------------------------------------------------------------
# Shared deterministic harness (real components, explicit test injection)
# ---------------------------------------------------------------------------


def _sandbox(root: Path) -> WorktreeSandboxBoundary:
    root.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws-af53m3w2r2",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        worktree_id=WORKTREE_ID,
        worktree_root=str(root.resolve()),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


class _RecordingReferenceAdapter(ReferenceFakeExecutorAdapter):
    """Explicit test double that records the real dispatched packages."""

    def __init__(self) -> None:
        super().__init__()
        self.packages: list = []

    def dispatch(self, package):
        self.packages.append(package)
        return super().dispatch(package)


def _dispatcher() -> tuple[ExecutionDispatcher, _RecordingReferenceAdapter]:
    registry = ExecutorRegistry()
    recording = _RecordingReferenceAdapter()
    registry.register(recording)
    dispatcher = ExecutionDispatcher(registry, state_store=InMemoryExecutionStateStore())
    return dispatcher, recording


def _task_main_binding(
    sandbox: WorktreeSandboxBoundary,
    *,
    canonical_task_id: str = "thin-main-1",
    legacy_context: object | None = None,
) -> CanonicalDispatchBinding:
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="af53-m3w2r2",
        objective="thin task-main binding",
        bounded_scope="thin task-main binding scope",
        validation_expectations=["focused"],
        semantic_stop_expectations=["stop"],
    )
    surface = create_role_tool_surface(
        "task-main",
        eager=("handoff.write", "handoff.open", "task.start"),
        progressive=(),
    )
    return CanonicalDispatchBinding(
        canonical_task_id=canonical_task_id,
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
        # None => trusted thin lifecycle classification (is_thin_task_lifecycle_binding).
        trusted_task_main_context=legacy_context,
    )


def _work_item_handoff(
    sandbox: WorktreeSandboxBoundary,
    *,
    role: str | None,
    work_item_id: str = "W1",
):
    semantic: dict = {
        "objective": f"bounded child objective {work_item_id}",
        "bounded_scope": f"bounded child scope {work_item_id}",
        "validation_expectations": ["focused thin lifecycle validation"],
        "semantic_stop_expectations": ["stop on role/handoff mismatch"],
    }
    if role is not None:
        semantic[WORK_ITEM_HANDOFF_ROLE_FIELD] = role
    return handoff_write(
        mode="work_item",
        semantic=semantic,
        caller_role="task-main",
        sandbox=sandbox,
        milestone_id="M1",
        work_item_id=work_item_id,
    )


def _start(binding: CanonicalDispatchBinding, ref: str, role: str):
    return dispatch_via_core("task.start", {"role": role, "handoff_ref": ref}, binding)


# ---------------------------------------------------------------------------
# R1 — production thin guidance exposes work_role + role equality
# ---------------------------------------------------------------------------


class TestR1ThinGuidanceExposesWorkRole:
    def test_thin_operation_guidance_makes_work_role_explicit(self) -> None:
        guidance = build_thin_task_main_operation_guidance()
        handoff_write_guidance = guidance["handoff.write"]
        assert handoff_write_guidance["work_item_role_field"] == WORK_ITEM_HANDOFF_ROLE_FIELD
        assert WORK_ITEM_HANDOFF_ROLE_FIELD in handoff_write_guidance["example"]["payload"]
        assert (
            handoff_write_guidance["example"]["payload"][WORK_ITEM_HANDOFF_ROLE_FIELD]
            == guidance["task.start"]["example"]["role"]
        )
        serialized = json.dumps(guidance, sort_keys=True)
        assert "payload.work_role" in serialized
        assert "task.start.role" in serialized
        assert "MUST equal" in handoff_write_guidance["role_equality"]
        # Generic mechanism: not reviewer-special; a permitted non-coder role
        # example is visible through the same field.
        assert WORK_ITEM_HANDOFF_ROLE_REQUIRED_ON_THIN_PATH is True

    def test_real_thin_role_bootstrap_receives_the_repaired_guidance(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "r1-bootstrap")
        binding = TrustedWorkerBinding(
            canonical_task_id="thin-main-r1",
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            trusted_context=bind_trusted_context(
                principal_id="task-main", principal_type="task-main", channel="mcp"
            ),
            handoff=TaskHandoff(
                work_role="task-main",
                task_kind="af53-m3w2r2",
                objective="thin task-main binding",
                bounded_scope="thin task-main binding scope",
                validation_expectations=["focused"],
                semantic_stop_expectations=["stop"],
            ),
            sandbox=sandbox,
            tool_surface=create_role_tool_surface(
                "task-main",
                eager=("handoff.write", "handoff.open", "task.start"),
                progressive=(),
            ),
            read_authorities=(),
            trusted_task_main_context=None,
        )
        payload = handle_role_bootstrap(binding, {})
        operation_guidance = payload["OPERATION_GUIDANCE"]
        assert operation_guidance == build_thin_task_main_operation_guidance()
        serialized = json.dumps(operation_guidance, sort_keys=True)
        assert "work_role" in serialized
        eager = " ".join(entry.get("materialized", "") for entry in payload["BASE_SKILLS"])
        assert "payload.work_role" in eager
        assert "task.start" in eager


# ---------------------------------------------------------------------------
# R2-R7 — role grounding + F2 integrity at the canonical ingress
# ---------------------------------------------------------------------------


class TestR2ToR5ExplicitRoleGrounding:
    @pytest.mark.parametrize("role", ("coder", "reviewer", "analyst"))
    def test_explicit_work_role_dispatches_for_every_permitted_role(
        self, tmp_path: Path, role: str
    ) -> None:
        sandbox = _sandbox(tmp_path / f"r2-{role}")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        ref = _work_item_handoff(sandbox, role=role, work_item_id="W1")

        response = _start(binding, ref.ref, role)

        assert response.ok is True, response.error
        assert recording.packages[0].working_context["work_role"] == role
        assert len(dispatcher.state_store.list_all()) == 1

    def test_thin_missing_work_role_fails_closed_with_zero_dispatch(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "r5-missing")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        ref = _work_item_handoff(sandbox, role=None, work_item_id="W1")

        response = _start(binding, ref.ref, "coder")

        assert response.ok is False
        assert response.error["code"] == "WORK_SCOPE_INSUFFICIENT"
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []
        assert THIN_PATH_MISSING_WORK_ROLE_FAILS_CLOSED is True
        assert THIN_WORK_ITEM_ROLE_EXPLICIT is True
        assert CONTROL_PLANE_DEFAULT_CHILD_ROLE_ON_THIN_PATH is False

    def test_thin_invalid_work_role_fails_closed_with_zero_dispatch(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "r5-invalid")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        ref = _work_item_handoff(sandbox, role="banana", work_item_id="W1")

        response = _start(binding, ref.ref, "coder")

        assert response.ok is False
        assert response.error["code"] == "WORK_SCOPE_INSUFFICIENT"
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []


class TestR6R7MismatchStillFailsClosed:
    def test_requested_reviewer_vs_grounded_coder(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "r6")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        ref = _work_item_handoff(sandbox, role="coder", work_item_id="W1")

        response = _start(binding, ref.ref, "reviewer")

        assert response.ok is False
        assert response.error["code"] == ROLE_HANDOFF_MISMATCH_CODE
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []

    def test_requested_coder_vs_grounded_reviewer(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "r7")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        ref = _work_item_handoff(sandbox, role="reviewer", work_item_id="W1")

        response = _start(binding, ref.ref, "coder")

        assert response.ok is False
        assert response.error["code"] == ROLE_HANDOFF_MISMATCH_CODE
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []


# ---------------------------------------------------------------------------
# R8-R9 — model-visible typed failure projection
# ---------------------------------------------------------------------------


def _mcp_call(server, operation: str, arguments: dict):
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "aota.invoke")
    result = tool.fn(operation=operation, arguments=arguments)
    structured = result.structuredContent if hasattr(result, "structuredContent") else None
    text = result.content[0].text if getattr(result, "content", None) else None
    return structured, text


class TestR8R9ModelVisibleTypedFailure:
    def _thin_mcp_server(self, sandbox: WorktreeSandboxBoundary):
        binding = TrustedWorkerBinding(
            canonical_task_id="thin-main-mcp",
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
            trusted_context=bind_trusted_context(
                principal_id="task-main", principal_type="task-main", channel="mcp"
            ),
            handoff=TaskHandoff(
                work_role="task-main",
                task_kind="af53-m3w2r2",
                objective="thin task-main binding",
                bounded_scope="thin task-main binding scope",
                validation_expectations=["focused"],
                semantic_stop_expectations=["stop"],
            ),
            sandbox=sandbox,
            tool_surface=create_role_tool_surface(
                "task-main",
                eager=("handoff.write", "handoff.open", "task.start"),
                progressive=(),
            ),
            read_authorities=(),
            trusted_task_main_context=None,
        )
        return create_shared_mcp_server(binding)

    def test_role_handoff_mismatch_projects_code_and_bounded_message(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "r8")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        ref = _work_item_handoff(sandbox, role="coder", work_item_id="W1")
        server = self._thin_mcp_server(sandbox)

        structured, text = _mcp_call(
            server, "task.start", {"role": "reviewer", "handoff_ref": ref.ref}
        )

        assert structured["ok"] is False
        assert structured["error"]["code"] == ROLE_HANDOFF_MISMATCH_CODE
        visible = json.loads(text)
        assert visible["error_code"] == ROLE_HANDOFF_MISMATCH_CODE
        message = visible["error_message"]
        assert "requested role 'reviewer'" in message
        assert "grounded durable handoff work_role 'coder'" in message
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []

    def test_model_visible_failure_carries_no_raw_exception_leak(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "r9")
        dispatcher, _ = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        ref = _work_item_handoff(sandbox, role="coder", work_item_id="W1")
        server = self._thin_mcp_server(sandbox)

        _structured, text = _mcp_call(
            server, "task.start", {"role": "reviewer", "handoff_ref": ref.ref}
        )

        assert "Traceback" not in text
        assert 'File "' not in text
        assert "ValueError" not in text
        assert "/home/" not in text
        assert "site-packages" not in text
        visible = json.loads(text)
        assert len(visible["error_message"]) <= 512


# ---------------------------------------------------------------------------
# R10 — legacy compatibility path preserved
# ---------------------------------------------------------------------------


class TestR10LegacyCompatibilityPreserved:
    def test_legacy_binding_keeps_historical_coder_default(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "r10")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        # Non-thin classification: trusted task-main context present (legacy
        # compatibility binding). No live plan view => no grounded-work gate.
        legacy_binding = _task_main_binding(
            sandbox, canonical_task_id="legacy-main-1", legacy_context=SimpleNamespace()
        )
        ref = _work_item_handoff(sandbox, role=None, work_item_id="W1")

        response = _start(legacy_binding, ref.ref, LEGACY_PATH_MISSING_WORK_ROLE_DEFAULT)

        assert response.ok is True, response.error
        assert recording.packages[0].working_context["work_role"] == "coder"
        assert len(dispatcher.state_store.list_all()) == 1


# ---------------------------------------------------------------------------
# R11 — reviewer tool authority (canonical policy)
# ---------------------------------------------------------------------------


class TestR11ReviewerToolAuthority:
    def test_reviewer_role_surface_is_read_and_test_only(self) -> None:
        surface = get_tool_surface_for_role("reviewer")
        names = set(surface.all_capability_names())
        assert "workspace.read" in names
        assert "workspace.search" in names
        assert "test.run" in names
        assert "workspace.write" not in names
        assert "restricted_shell.run" not in names

    def test_reviewer_worker_binding_matches_canonical_policy(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "r11-binding"
        root.mkdir(parents=True, exist_ok=True)
        handoff = TaskHandoff(
            work_role="reviewer",
            task_kind="af53-m3w2r2-review",
            objective="review bounded artifact",
            bounded_scope="read-only review of the bounded artifact",
            validation_expectations=["read artifact", "report verdict"],
            semantic_stop_expectations=["stop on missing artifact"],
        )
        binding = build_worker_binding(
            root=root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            canonical_task_id="af53m3w2r2:reviewer:1",
            handoff=handoff,
        )
        names = set(binding.tool_surface.all_capability_names())
        assert binding.handoff.work_role.value == "reviewer"
        assert "workspace.read" in names
        assert "test.run" in names
        assert "workspace.write" not in names


# ---------------------------------------------------------------------------
# R12 — no reviewer workflow state required
# ---------------------------------------------------------------------------


class TestR12NoReviewerWorkflowState:
    def test_reviewer_starts_without_legacy_workflow_state(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "r12"
        sandbox = _sandbox(root)
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _task_main_binding(sandbox)
        ref = _work_item_handoff(sandbox, role="reviewer", work_item_id="W1")

        response = _start(binding, ref.ref, "reviewer")

        assert response.ok is True, response.error
        assert binding.trusted_task_main_context is None
        package = recording.packages[0]
        assert package.working_context["work_role"] == "reviewer"
        for workflow_key in (
            "review_count",
            "review_position",
            "INTEGRATED_REVIEW_REQUIRED",
            "DISPATCHED_REVIEW",
        ):
            assert workflow_key not in package.working_context
        files = [path for path in root.rglob("*") if path.is_file()]
        assert files
        for path in files:
            assert path.relative_to(root).parts[:2] == (".aota", "handoffs")
        assert tpb.REVIEWER_SPECIAL_WORKFLOW_STATE_REQUIRED is False
        assert tpb.THIN_MISSING_WORK_ROLE_FAILS_CLOSED is True
        assert tpb.LEGACY_MISSING_WORK_ROLE_CODER_DEFAULT_PRESERVED is True
