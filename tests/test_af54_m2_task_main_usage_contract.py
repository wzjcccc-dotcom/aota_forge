"""AF #54 M2/W1-W2 — thin task-main usage contract + work-identity convergence.

Bounded focused proof of the frozen M2 contract (M1 gap map G54-01/02/03/06):

  T1  thin work_item grounding requires the explicit semantic work identity:
      missing work_item_ref / milestone_ref -> typed WORK_SCOPE_INSUFFICIENT
      before dispatch, zero child execution, no invented W1/M1 identity
  T2  explicit non-M1 Work identity dispatches AND the canonical task
      identity carries the supplied Milestone/Work segments (the historical
      envelope-or-M1/W1 stamp could contradict the grounded handoff and
      fail the Worker binding consistency gate)
  T3  the direct TaskHandoff payload shape is covered by the same gate
  T4  legacy compatibility keeps its historical W1/M1 tail unchanged
      (bounded to the legacy derivation path only)
  T5  frozen contract markers (CP invents neither role nor Work identity)
  T6  the task-main Role Skill is current thin production guidance: the
      legacy coordinator normal path (activate/recover/advance_once,
      integrated-review transition, reviewer-delegation prohibition) is
      gone, and the canonical semantic contract + typed-error recovery +
      bounded review sizing are taught there (single procedural owner)
  T7  eager/affordance channels stay minimal and non-contradictory:
      discovery pointer to the Skill, first-use invariants, example carries
      the semantic identity fields
  T8  thin task-main can still discover + open the canonical Skill

PROVES=bounded model-visible contract + CP semantic-identity mechanics at the
       canonical ingress seam with explicit test doubles.
DOES_NOT_PROVE=real Hermes task-main LLM behavior (M2/W4 bounded smoke) or
       production telemetry (M3).
"""

from __future__ import annotations

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
from aota_forge.work_plane.handoff_store import handoff_open, handoff_write
from aota_forge.work_plane.handoff_runtime import WorkScopeInsufficientError
from aota_forge.work_plane.task_facade import (
    CONTROL_PLANE_DEFAULTS_MILESTONE_TO_M1_ON_THIN_PATH,
    CONTROL_PLANE_DEFAULTS_WORK_ITEM_TO_W1_ON_THIN_PATH,
    CONTROL_PLANE_INVENTS_SEMANTIC_WORK_IDENTITY,
    LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_MILESTONE,
    LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_WORK_ITEM,
    THIN_PATH_MISSING_WORK_IDENTITY_FAILS_CLOSED,
    THIN_WORK_ITEM_SEMANTIC_IDENTITY_EXPLICIT,
    load_trusted_work_item_task_handoff,
)
from aota_forge.work_plane.task_main_descriptors import (
    build_thin_task_main_operation_guidance,
)
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

PROJECT_ID = "proj-af54m2"
WORKTREE_ID = "wt-af54m2"

_SKILL_MD = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "aota-task-main-control"
    / "SKILL.md"
)


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    yield
    reset_execution_dispatcher()


def _sandbox(root: Path) -> WorktreeSandboxBoundary:
    root.mkdir(parents=True, exist_ok=True)
    return WorktreeSandboxBoundary(
        workspace_id="ws-af54m2",
        workspace_root=str(root),
        project_id=PROJECT_ID,
        project_root=str(root),
        worktree_id=WORKTREE_ID,
        worktree_root=str(root.resolve()),
        registry_fingerprint="0" * 64,
        candidate_fingerprint="1" * 64,
    )


class _RecordingReferenceAdapter(ReferenceFakeExecutorAdapter):
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


def _thin_task_main_binding(sandbox: WorktreeSandboxBoundary) -> CanonicalDispatchBinding:
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="af54-m2",
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
        canonical_task_id="thin-main-af54",
        project_id=sandbox.project_id,
        worktree_id=sandbox.worktree_id,
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=surface,
        # None => thin lifecycle classification (is_thin_task_lifecycle_binding)
        trusted_task_main_context=None,
    )


def _write_via_ingress(binding: CanonicalDispatchBinding, payload: dict) -> dict:
    return dispatch_via_core(
        "handoff.write", {"mode": "work_item", "payload": payload}, binding
    )


def _payload(**overrides) -> dict:
    base = {
        "work_role": "reviewer",
        "objective": "Review W1 result",
        "bounded_scope": "Only W1 changed files and W1 validation evidence",
        "validation_expectations": ["focused review checks"],
        "semantic_stop_expectations": ["stop when scope insufficient"],
    }
    base.update(overrides)
    return base


def _start(binding: CanonicalDispatchBinding, ref: str, role: str = "reviewer") -> dict:
    return dispatch_via_core("task.start", {"role": role, "handoff_ref": ref}, binding)


class TestT1ThinMissingWorkIdentityFailsClosed:
    def test_missing_work_item_ref_typed_fail_closed_zero_dispatch(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "t1-wid")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _thin_task_main_binding(sandbox)
        # Production thin write: envelope stays ungrounded; payload omits
        # work_item_ref (only milestone_ref supplied).
        written = _write_via_ingress(binding, _payload(milestone_ref="M2"))
        assert written.ok, written.error
        response = _start(binding, written.payload["ref"])
        assert response.ok is False
        assert response.error["code"] == "WORK_SCOPE_INSUFFICIENT"
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []

    def test_missing_milestone_ref_typed_fail_closed_zero_dispatch(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "t1-mid")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _thin_task_main_binding(sandbox)
        written = _write_via_ingress(binding, _payload(work_item_ref="W1"))
        assert written.ok, written.error
        response = _start(binding, written.payload["ref"])
        assert response.ok is False
        assert response.error["code"] == "WORK_SCOPE_INSUFFICIENT"
        assert recording.packages == []
        assert dispatcher.state_store.list_all() == []

    def test_failure_message_is_bounded_actionable_and_invents_nothing(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "t1-msg")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _thin_task_main_binding(sandbox)
        written = _write_via_ingress(binding, _payload())
        response = _start(binding, written.payload["ref"])
        assert response.ok is False
        message = response.error["message"]
        assert "work_item_ref" in message
        assert "Traceback" not in message
        assert str(sandbox.worktree_root) not in message
        assert recording.packages == []


class TestT2ExplicitNonM1IdentityDispatchesAndStampAgrees:
    def test_explicit_m2_w7_identity_dispatches_with_matching_stamp(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "t2")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _thin_task_main_binding(sandbox)
        written = _write_via_ingress(
            binding, _payload(work_item_ref="W7", milestone_ref="M2")
        )
        assert written.ok, written.error
        response = _start(binding, written.payload["ref"])
        assert response.ok is True, response.error
        task_id = response.payload["task_id"]
        assert f":{PROJECT_ID}:M2:W7:" in f":{task_id}:"
        assert ":M1:W1:" not in task_id
        package = recording.packages[0]
        refs = package.working_context["refs"]
        assert refs["work_item_ref"]["ref"] == "W7"
        assert refs["milestone_ref"]["ref"] == "M2"
        assert package.working_context["work_role"] == "reviewer"


class TestT3DirectTaskHandoffShapeSameGate:
    def test_direct_shape_missing_refs_fails_closed(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "t3")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _thin_task_main_binding(sandbox)
        # work_role + task_kind present => direct TaskHandoff derivation shape
        written = _write_via_ingress(binding, _payload(task_kind="af54-m2-direct"))
        assert written.ok, written.error
        response = _start(binding, written.payload["ref"])
        assert response.ok is False
        assert response.error["code"] == "WORK_SCOPE_INSUFFICIENT"
        assert recording.packages == []

    def test_direct_shape_with_refs_dispatches(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "t3b")
        dispatcher, recording = _dispatcher()
        bind_execution_dispatcher(dispatcher)
        binding = _thin_task_main_binding(sandbox)
        written = _write_via_ingress(
            binding,
            _payload(task_kind="af54-m2-direct", work_item_ref="W3", milestone_ref="M1"),
        )
        assert written.ok, written.error
        response = _start(binding, written.payload["ref"])
        assert response.ok is True, response.error
        assert f":M1:W3:" in f":{response.payload['task_id']}:"


class TestT4LegacyFallbackBounded:
    def test_legacy_derivation_keeps_historical_w1_m1_tail(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "t4")
        written = handoff_write(
            mode="work_item",
            semantic=_payload(work_role="coder"),
            caller_role="task-main",
            sandbox=sandbox,
        )
        opened = handoff_open(written.ref, "full", sandbox=sandbox)
        # legacy compatibility shape (no explicit thin requirements)
        handoff = load_trusted_work_item_task_handoff(opened=opened, sandbox=sandbox)
        assert handoff.work_item_ref is not None
        assert (
            handoff.work_item_ref.ref
            == LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_WORK_ITEM
        )
        assert handoff.milestone_ref is not None
        assert (
            handoff.milestone_ref.ref
            == LEGACY_PATH_MISSING_WORK_IDENTITY_DEFAULT_MILESTONE
        )
        # explicit role requirement unchanged on legacy derivation
        assert handoff.work_role.value == "coder"

    def test_thin_derivation_flag_raises_instead_of_defaulting(self, tmp_path: Path) -> None:
        sandbox = _sandbox(tmp_path / "t4b")
        written = handoff_write(
            mode="work_item",
            semantic=_payload(),
            caller_role="task-main",
            sandbox=sandbox,
        )
        opened = handoff_open(written.ref, "full", sandbox=sandbox)
        with pytest.raises(WorkScopeInsufficientError) as raised:
            load_trusted_work_item_task_handoff(
                opened=opened,
                sandbox=sandbox,
                require_explicit_work_role=True,
                require_explicit_work_identity=True,
            )
        assert raised.value.code == "WORK_SCOPE_INSUFFICIENT"


class TestT5FrozenMarkers:
    def test_cp_never_invents_role_or_work_identity_on_thin(self) -> None:
        assert THIN_WORK_ITEM_SEMANTIC_IDENTITY_EXPLICIT is True
        assert THIN_PATH_MISSING_WORK_IDENTITY_FAILS_CLOSED is True
        assert CONTROL_PLANE_DEFAULTS_WORK_ITEM_TO_W1_ON_THIN_PATH is False
        assert CONTROL_PLANE_DEFAULTS_MILESTONE_TO_M1_ON_THIN_PATH is False
        assert CONTROL_PLANE_INVENTS_SEMANTIC_WORK_IDENTITY is False


class TestT6TaskMainSkillIsThinCanonical:
    @pytest.fixture(scope="class")
    def skill_text(self) -> str:
        return _SKILL_MD.read_text(encoding="utf-8")

    def test_legacy_coordinator_normal_path_is_gone(self, skill_text: str) -> None:
        for stale in (
            "task_main.activate_milestone",
            "task_main.recover_coordinator",
            "task_main.advance_once",
            "INTEGRATED_REVIEW_REQUIRED",
            "Never construct the review handoff manually",
            "AF dispatches the governed integrated reviewer",
        ):
            assert stale not in skill_text, stale

    def test_canonical_semantic_contract_is_taught(self, skill_text: str) -> None:
        for marker in (
            "work_role",
            "work_item_ref",
            "milestone_ref",
            "objective",
            "bounded_scope",
            "ROLE_HANDOFF_MISMATCH",
            "WORK_SCOPE_INSUFFICIENT",
            "task.start",
            "handoff.write",
        ):
            assert marker in skill_text, marker

    def test_work_identity_ownership_and_distinction(self, skill_text: str) -> None:
        assert "canonical_task_id" in skill_text
        assert "attempt" in skill_text
        assert "handoff_ref" in skill_text
        assert "execution order" in skill_text
        assert "new W number" in skill_text

    def test_typed_error_recovery_and_review_sizing(self, skill_text: str) -> None:
        assert "do not retry unchanged" in skill_text
        assert "Worker budget" in skill_text
        assert "decompose" in skill_text
        assert "fixed reviewer count" in skill_text

    def test_preserved_useful_discipline(self, skill_text: str) -> None:
        for discipline in ("card-first", "needs_input", "bounded_scope", "role.bootstrap"):
            assert discipline in skill_text, discipline

    def test_skill_metadata_no_longer_advertises_legacy_cycle(self) -> None:
        import re

        short, use_when = af_roles._SKILL_META["aota-task-main-control"]
        text = (short + " " + use_when).lower()
        # the legacy coordinator cycle verbs (activate/recover/advance the
        # milestone) must be gone; "recovery" as generic typed-failure
        # handling is not the legacy verb.
        for stale in ("activate", "recover", "advance"):
            assert re.search(rf"\b{stale}\b", text) is None, stale
        assert "skill" in text or "handoff" in text


class TestT7MinimalNonContradictoryAffordances:
    def test_thin_eager_guidance_is_discovery_pointer_not_manual(self) -> None:
        guidance = af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE
        # AF #59 M2 acceptance-polish R1/R2: the historical 1200-char guard
        # moved to the canonical thin discovery-pointer bound (still far below
        # the per-Skill bootstrap bound and enforced by role_bootstrap).
        assert len(guidance) <= af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE_MAX_CHARS
        assert "payload.work_role" in guidance
        assert "work_item_ref" in guidance
        assert "milestone_ref" in guidance
        assert "aota-task-main-control" in guidance
        assert "skill.open" in guidance
        for stale in ("activate", "advance_once", "INTEGRATED_REVIEW"):
            assert stale not in guidance

    def test_operation_guidance_stays_thin_affordance(self) -> None:
        thin = build_thin_task_main_operation_guidance()
        # AF #54 M5/W3: thin primitive contracts for the governed git/github
        # lifecycle + a governance-Skill pointer were added; procedures stay
        # in the progressive Skill (verified by test_af54_m5_w3).
        assert set(thin) == {
            "normal_path", "handoff.write", "task.start",
            "git", "github", "governance_procedure",
        }
        example = thin["handoff.write"]["example"]["payload"]
        assert example["work_item_ref"] == "W1"
        assert example["milestone_ref"] == "M2"
        assert example["work_role"] == "reviewer"
        note = thin["handoff.write"]["note"]
        assert "task-main Skill" in note
        assert "MUST equal" in thin["handoff.write"]["role_equality"]
        # not a second tutorial: recovery tables and identity lectures stay
        # in the Role Skill, not in the operation affordance
        rendered = str(thin)
        assert "typed-error recovery" not in rendered
        assert "WORK_SCOPE_INSUFFICIENT" not in rendered

    def test_legacy_curated_channel_stays_bounded_to_legacy(self) -> None:
        # the legacy eager copy keeps its historical coordinator prose
        # (compatibility only); it is never the thin materialization.
        legacy = af_roles.curated_eager_guidance("aota-task-main-control")
        assert "activate" in legacy
        assert legacy != af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE


class TestT8ThinBootstrapDiscoversCanonicalSkill:
    def test_thin_task_main_bootstrap_exposes_ref_and_open_returns_canonical(
        self, tmp_path: Path
    ) -> None:
        sandbox = _sandbox(tmp_path / "t8")
        binding = _thin_task_main_binding(sandbox)
        bootstrap = dispatch_via_core("role.bootstrap", {}, binding)
        assert bootstrap.ok, bootstrap.error
        base = bootstrap.payload["BASE_SKILLS"]
        entry = next(e for e in base if e["skill_id"] == "aota-task-main-control")
        assert entry["materialized"] == af_roles.THIN_TASK_MAIN_EAGER_GUIDANCE
        opened = dispatch_via_core("skill.open", {"ref": entry["ref"]}, binding)
        assert opened.ok, opened.error
        body = str(opened.payload)
        assert "work_item_ref" in body
        assert "ROLE_HANDOFF_MISMATCH" in body
        assert "advance_once" not in body
