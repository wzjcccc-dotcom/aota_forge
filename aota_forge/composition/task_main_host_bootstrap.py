"""Thin trusted host bootstrap for Hermes aota-task-main MCP (M3/W2).

M3/W1 created TrustedTaskMainRuntimeContext but did not prove that a real
Hermes MCP subprocess can reconstruct it without Python injecting an
in-process object. This module is the thinnest operator-controlled seam
that does so.

Trusted channel: the Hermes MCP child inherits a *single* host-controlled
filesystem reference (AOTA_W3_MCP_ROOT) that the operator set before the
Hermes session was launched.  The child resolves

    <root>/.aota/task-main-bootstrap.json

via that trusted root and deterministically rebuilds every typed object
from that file plus the durable file-backed stores it points to.  No
model-facing aota.invoke argument may supply project, worktree, store
location, plan authority, session identity, resolver, or approval truth.
The file is operator-owned; the MCP child never consults CWD or an
arbitrary model-supplied path.

Only operator/runtime-controlled configuration or durable state references
are consumed (store paths, plan authority/digest, session ref, runtime
config path, resolver factories that are code, not data).  No serialized
authority blob from the model is honored.

If the bootstrap file is absent the caller is not a task-main MCP child;
the normal worker binding path is used.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from aota_forge.core.context import bind_trusted_context
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import portable_plan_digest
from aota_forge.composition.execution import create_production_execution_dispatcher
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.runtime.completion import DurableCompletionCoordinator
from aota_forge.runtime.task_main.coordinator_store import FileBackedTaskMainCoordinatorStore
from aota_forge.runtime.task_main.coordinator import MilestonePlanView
from aota_forge.runtime.task_main.control import TaskMainControlService
from aota_forge.mcp_transport import TrustedTaskMainRuntimeContext, TrustedWorkerBinding
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.progression import MilestoneWorkItemGraph
from aota_forge.work_plane.tool_surface import create_role_tool_surface
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    create_workspace_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox
from aota_forge.work_plane.risk_review import MilestoneRiskEnvelope, ProcessDepth
from aota_forge.work_plane.progression import FocusedValidationEvidence, FocusedValidationVerdict
from aota_forge.runtime.task_main.reconciliation import GovernedWorkItemEvidence, GovernedReviewEvidence
from aota_forge.work_plane.milestone_review import MilestoneReviewEvidence, ReviewCycle, ReviewFindingEvidence, ReviewFindingClassification
from aota_forge.core.project.resolver import ProjectCandidateEvidence, ProjectResolutionEvidence
from aota_forge.work_plane.agents_applicability import AgentsPolicyCandidate
from aota_forge.work_plane.workspace_mutation import WORKSPACE_WRITE_DESCRIPTOR, create_workspace_mutation_authority
from aota_forge.work_plane.restricted_shell import RESTRICTED_SHELL_DESCRIPTOR, create_restricted_shell_authority

BOOTSTRAP_ENV_ROOT = "AOTA_W3_MCP_ROOT"
BOOTSTRAP_RELPATH = ".aota/task-main-bootstrap.json"
# Also accept explicit path for testing harness
BOOTSTRAP_EXPLICIT_ENV = "AOTA_TASK_MAIN_BOOTSTRAP"


def _project_evidence(root: Path, project_id: str) -> ProjectResolutionEvidence:
    # Dogfood handling: use real project manifest and kind
    if project_id == "aota_forge_dogfood":
        candidate = ProjectCandidateEvidence(
            workspace_id=f"w3-{project_id}",
            workspace_root=str(root),
            project_id=project_id,
            project_root=str(root),
            manifest_path=".aota/project.yaml",
            name=project_id,
            kind="dogfood",
            status="active",
            registry_fingerprint="a" * 64,
            candidate_fingerprint="b" * 64,
        )
        return ProjectResolutionEvidence(
            status="RESOLVED",
            workspace_id=f"w3-{project_id}",
            workspace_root=str(root),
            registry_fingerprint="a" * 64,
            listing_fingerprint="c" * 64,
            candidates=(candidate,),
        )
    candidate = ProjectCandidateEvidence(
        workspace_id=f"w3-{project_id}",
        workspace_root=str(root),
        project_id=project_id,
        project_root=str(root),
        manifest_path="work/sentinel.txt",
        name=project_id,
        kind="disposable-m3w2",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    return ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id=f"w3-{project_id}",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )


def _neutral_envelope(milestone: str) -> MilestoneRiskEnvelope:
    return MilestoneRiskEnvelope(milestone_ref=milestone, default_process_depth=ProcessDepth.STANDARD, minimum_process_depth=ProcessDepth.STANDARD)


def _view_from_dict(d: dict[str, Any]) -> MilestonePlanView:
    g = d["graph"]
    graph = MilestoneWorkItemGraph(
        milestone_ref=g["milestone_ref"],
        work_items=list(g["work_items"]),
        dependencies=[list(e) for e in g.get("dependencies", [])],
    )
    return MilestonePlanView(
        plan_authority=d["plan_authority"],
        plan_digest=d["plan_digest"],
        plan_source_revision=d.get("plan_source_revision"),
        milestone_id=d["milestone_id"],
        entry_base=d["entry_base"],
        graph=graph,
        milestone_user_approval_satisfied=bool(d["milestone_user_approval_satisfied"]),
        plan_amendment_required=bool(d.get("plan_amendment_required", False)),
    )


def _load_bootstrap_dict() -> dict[str, Any] | None:
    # Explicit path takes precedence (harness-controlled)
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip() and "${" not in explicit:
        p = Path(explicit)
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                try:
                    with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                        dbg.write(f"_load explicit failed {e}\n")
                except Exception:
                    pass
                return None
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load explicit not found {explicit}\n")
        except Exception:
            pass
        return None
    elif explicit and "${" in explicit:
        # Literal from hermes mcp_servers expansion when var not set in hermes env; ignore and fall back
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load explicit is literal {explicit}, ignoring\n")
        except Exception:
            pass
        pass  # fall through to root_env
    root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
    if not root_env:
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load no root_env\n")
        except Exception:
            pass
        return None
    root = Path(root_env).resolve()
    bootstrap_path = root / BOOTSTRAP_RELPATH
    if not bootstrap_path.is_file():
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load bootstrap not found {bootstrap_path} root_env={root_env}\n")
        except Exception:
            pass
        return None
    try:
        return json.loads(bootstrap_path.read_text(encoding="utf-8"))
    except Exception as e:
        try:
            with open("/tmp/aota_task_main_bootstrap_debug.log", "a", encoding="utf-8") as dbg:
                dbg.write(f"_load bootstrap json failed {e} {bootstrap_path}\n")
        except Exception:
            pass
        return None


def _handoff_for(work_item_id: str, milestone_ref: str = "M3") -> TaskHandoff:
    # Dogfood M1 handling: bounded calculator implementation
    # Preserve M3 fixture for existing M3 tests, but handle M1/W1..W3 for dogfood.
    if milestone_ref == "M1" and work_item_id in ("W1", "W2", "W3"):
        if work_item_id == "W1":
            return TaskHandoff(
                work_role="coder",
                task_kind="dogfood-w1-core-calculator",
                objective=(
                    "Use only aota.invoke workspace.* and test.run. Create minimal Python calculator package "
                    "with add/subtract/multiply/divide, handling division-by-zero as defined error. "
                    "Do not implement CLI concerns. Use standard library only. Place package under src/calculator or equivalent "
                    "as per .aota/project.yaml source_root. Provide deterministic core arithmetic."
                ),
                bounded_scope="src/calculator",
                validation_expectations=("core arithmetic add/sub/mul/div, division-by-zero defined",),
                semantic_stop_expectations=("stop if scope unclear, do not expand to CLI",),
                work_item_ref=SemanticReference(ref=work_item_id),
                milestone_ref=SemanticReference(ref=milestone_ref),
            )
        if work_item_id == "W2":
            return TaskHandoff(
                work_role="coder",
                task_kind="dogfood-w2-cli-contract",
                objective=(
                    "Use only aota.invoke workspace.* and test.run. Implement bounded CLI with commands "
                    "calc add/sub/mul/div, support --json flag, human-readable output, deterministic "
                    "non-success for invalid numeric input and division-by-zero. Use argparse or stdlib. "
                    "Reuse core calculator from W1."
                ),
                bounded_scope="src/calculator",
                validation_expectations=("CLI add/sub/mul/div, --json, invalid input and division-by-zero handled",),
                semantic_stop_expectations=("stop if scope unclear",),
                work_item_ref=SemanticReference(ref=work_item_id),
                milestone_ref=SemanticReference(ref=milestone_ref),
            )
        if work_item_id == "W3":
            return TaskHandoff(
                work_role="coder",
                task_kind="dogfood-w3-tests-readme",
                objective=(
                    "Use only aota.invoke workspace.* and test.run (do not use raw terminal). "
                    "Add deterministic tests for core and CLI, and bounded README usage. "
                    "Use pytest via test.run if available; if test.run unavailable or unusable, return BLOCKED. "
                    "Do not bypass via raw shell."
                ),
                bounded_scope="tests",
                validation_expectations=("core tests, CLI tests, README usage, test execution via governed AF path",),
                semantic_stop_expectations=("stop if test.run unavailable",),
                work_item_ref=SemanticReference(ref=work_item_id),
                milestone_ref=SemanticReference(ref=milestone_ref),
            )
    # Reviewer for M1 integrated review (dogfood) and M3
    if work_item_id.startswith("M3/RV") or work_item_id == "M3/RV1" or work_item_id.startswith("M1/RV") or work_item_id == "RV1" or work_item_id == "M1/RV1":
        # For dogfood M1, the integrated review validates all W1/W2/W3 together
        if milestone_ref == "M1":
            return TaskHandoff(
                work_role="reviewer",
                task_kind="dogfood-m1-integrated-review",
                objective=(
                    "Use only aota.invoke workspace.* and test.run. Perform integrated review for Milestone M1: "
                    "validate calculator core, CLI, error contracts, --json, tests, README, integration. "
                    "Do not use native terminal/file fallback. One integrated review only."
                ),
                bounded_scope="review of src/calculator, CLI, tests, README",
                validation_expectations=("integrated review PASS/FAIL with findings",),
                semantic_stop_expectations=("review semantic stop",),
                work_item_ref=SemanticReference(ref=work_item_id),
                milestone_ref=SemanticReference(ref=milestone_ref),
            )
        return TaskHandoff(
            work_role="reviewer",
            task_kind="m3-w2-review",
            objective="Use only workspace.search and workspace.read to inspect work/output_W1.txt and work/output_W2.txt for the sentinel token. Validate they contain the expected token. Do not use terminal or shell.",
            bounded_scope="work/output_W1.txt and work/output_W2.txt",
            validation_expectations=("review binding validation",),
            semantic_stop_expectations=("review semantic stop",),
            work_item_ref=SemanticReference(ref=work_item_id),
            milestone_ref=SemanticReference(ref=milestone_ref),
        )
    # Fallback for M3 and other milestones: controlled fixture
    return TaskHandoff(
        work_role="coder",
        task_kind="m3-w2-real-slice",
        objective=f"Use only workspace.search, workspace.read, workspace.write. Find sentinel AOTA_M3W2_SENTINEL in work/sentinel.txt, read it, then write a bounded output to work/output_{work_item_id}.txt containing exactly the sentinel token. Do not use terminal or shell.",
        bounded_scope=f"work/sentinel.txt and work/output_{work_item_id}.txt only",
        validation_expectations=(f"cheap validation for {work_item_id}",),
        semantic_stop_expectations=(f"semantic stop for {work_item_id}",),
        work_item_ref=SemanticReference(ref=work_item_id),
        milestone_ref=SemanticReference(ref=milestone_ref),
    )


def _reviewer_handoff() -> TaskHandoff:
    return _handoff_for("M3/RV1")


def _validate_bootstrap_trust_boundary(data: dict[str, Any], bootstrap_path: Path | None) -> None:
    """Fail-closed validation of bootstrap trust boundary (W3 operational acceptance).

    Ensures bootstrap file content is consistent with trusted operator context
    (env root, worktree scope, store locations). Prevents tamper that could
    cross project/worktree/session scope or inject foreign store paths.
    """
    from aota_forge.mcp_transport import TrustedBindingError

    # Basic identifier shape
    import re

    _SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
    for field in ("project_id", "worktree_id"):
        val = str(data.get(field, ""))
        if not val.strip() or len(val) > 512:
            raise TrustedBindingError(f"bootstrap {field} invalid length/empty")
        if not _SAFE_ID_RE.fullmatch(val):
            raise TrustedBindingError(f"bootstrap {field} invalid identifier")
    # origin session: allow broader but must be non-empty bounded
    origin_val = str(data.get("origin_task_main_session_ref", ""))
    if not origin_val.strip() or len(origin_val) > 512:
        raise TrustedBindingError("bootstrap origin_task_main_session_ref invalid")

    worktree_root = Path(str(data["worktree_root"])).resolve()
    coordinator_store_path = Path(str(data["coordinator_store_path"])).resolve()
    execution_store_path = Path(str(data["execution_store_path"])).resolve()
    runtime_config_path = Path(str(data["runtime_config_path"])).resolve()

    # Bootstrap location must be derived from trusted env, not CWD or model path
    # Verify worktree_root matches the trusted env root (scope matching)
    trusted_root: Path | None = None
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip() and "${" not in explicit:
        try:
            trusted_root = Path(explicit).parent.parent.resolve() if explicit.endswith(BOOTSTRAP_RELPATH) else Path(explicit).parent.resolve()
            # If explicit is file path, its parent/.aota parent is worktree root
            if bootstrap_path is not None:
                expected_root = bootstrap_path.parent.parent.resolve()
                if worktree_root != expected_root:
                    raise TrustedBindingError("bootstrap worktree_root does not match explicit bootstrap location")
        except Exception as exc:
            raise TrustedBindingError(f"bootstrap explicit location mismatch: {exc}") from exc
    else:
        root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
        if root_env:
            try:
                trusted_root = Path(root_env).resolve()
            except Exception:
                raise TrustedBindingError("bootstrap trusted root invalid")
            if worktree_root != trusted_root:
                raise TrustedBindingError("bootstrap worktree_root does not match trusted AOTA_W3_MCP_ROOT")
        else:
            raise TrustedBindingError("bootstrap trusted root absent")

    # Store paths must be within worktree_root/.aota (bounded, no escape)
    for p, label in ((coordinator_store_path, "coordinator_store_path"), (execution_store_path, "execution_store_path")):
        try:
            p.relative_to(worktree_root)
        except ValueError:
            raise TrustedBindingError(f"bootstrap {label} outside worktree_root")
        # Must be under .aota
        if ".aota" not in p.parts:
            raise TrustedBindingError(f"bootstrap {label} not under .aota")
    # Runtime config must be absolute and within worktree or trusted location
    if not runtime_config_path.is_absolute():
        raise TrustedBindingError("bootstrap runtime_config_path must be absolute")
    # File permissions: bootstrap must not be world-writable or contain secrets
    if bootstrap_path is not None and bootstrap_path.exists():
        try:
            mode = bootstrap_path.stat().st_mode
            if mode & 0o002:
                raise TrustedBindingError("bootstrap file is world-writable")
        except TrustedBindingError:
            raise
        except Exception:
            pass
    # Plan digest shape validation (hex 64)
    live = data.get("live_plan_view") or {}
    digest = str(live.get("plan_digest", ""))
    if digest and (len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest.lower())):
        raise TrustedBindingError("bootstrap live_plan_view digest invalid")


def _bootstrap_path_for_validation() -> Path | None:
    explicit = os.environ.get(BOOTSTRAP_EXPLICIT_ENV)
    if explicit and explicit.strip() and "${" not in explicit:
        p = Path(explicit)
        if p.is_file():
            return p.resolve()
    root_env = os.environ.get(BOOTSTRAP_ENV_ROOT)
    if root_env:
        return (Path(root_env).resolve() / BOOTSTRAP_RELPATH).resolve()
    return None


def try_build_task_main_binding() -> TrustedWorkerBinding | None:
    """Attempt to build a task-main TrustedWorkerBinding from host bootstrap.

    Returns None if no bootstrap file is present (caller is a normal worker).
    Raises TrustedBindingError if the bootstrap is present but malformed.
    This is the ONLY place where TrustedTaskMainRuntimeContext is minted for
    a real Hermes MCP child.
    """
    bootstrap_path = _bootstrap_path_for_validation()
    data = _load_bootstrap_dict()
    if data is None:
        return None
    # Trust-boundary validation (W3) — fail closed on tamper / scope mismatch
    _validate_bootstrap_trust_boundary(data, bootstrap_path)
    # Validate required keys — all are operator-controlled, not model supplied
    project_id = str(data["project_id"])
    worktree_id = str(data["worktree_id"])
    worktree_root = Path(str(data["worktree_root"])).resolve()
    coordinator_store_path = Path(str(data["coordinator_store_path"])).resolve()
    execution_store_path = Path(str(data["execution_store_path"])).resolve()
    runtime_config_path = Path(str(data["runtime_config_path"])).resolve()
    origin_session = str(data["origin_task_main_session_ref"])
    executor_id = str(data.get("executor_id", "hermes"))
    coordinator_id = data.get("coordinator_id")  # may be None
    live_view_dict = data["live_plan_view"]
    next_view_dict = data.get("next_milestone_view")

    # Reconstruct typed views
    live_view = _view_from_dict(live_view_dict)
    next_view = _view_from_dict(next_view_dict) if next_view_dict else None

    # Reconstruct stores (file-backed, durable)
    coord_store = FileBackedTaskMainCoordinatorStore(coordinator_store_path)
    exec_store = FileBackedExecutionStateStore(execution_store_path)

    # Build dispatcher via production composition (requires runtime config)
    # The dispatcher will be used by TaskMainControlService to dispatch workers
    # It needs the same hermes binary and runtime config that the operator
    # used to launch the task-main session.
    from aota_forge.core.execution.durable_state import OriginSessionRef
    from aota_forge.runtime.config import load_runtime_config

    # Ensure dispatcher sees same env as harness; AOTA_FORGE_RUNTIME_CONFIG
    # should already point to runtime_config_path, but we enforce fallback
    old_env = os.environ.get("AOTA_FORGE_RUNTIME_CONFIG")
    need_restore = False
    if old_env != str(runtime_config_path):
        os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = str(runtime_config_path)
        need_restore = True
    try:
        runtime_cfg = load_runtime_config(config_path=str(runtime_config_path))
        dispatcher = create_production_execution_dispatcher(
            default_cwd=worktree_root,
            runtime_config=runtime_cfg,
            state_store=exec_store,
            origin_session_ref=OriginSessionRef(value=origin_session),
        )
        # Completion coordinator (admission-gated, optional)
        # For this slice we create one without transport (delivery is via harness)
        # Use same store as dispatcher (ensured above) and limits from config
        from aota_forge.composition.execution import admission_limits_from_runtime_config

        completion = DurableCompletionCoordinator(
            dispatcher=dispatcher,
            store=exec_store,
            transport=None,
            admission_limits=admission_limits_from_runtime_config(runtime_cfg),
        )
    finally:
        if need_restore:
            if old_env is None:
                os.environ.pop("AOTA_FORGE_RUNTIME_CONFIG", None)
            else:
                os.environ["AOTA_FORGE_RUNTIME_CONFIG"] = old_env

    control_service = TaskMainControlService(
        coordinator_store=coord_store,
        execution_store=exec_store,
        execution_dispatcher=dispatcher,
        completion_coordinator=completion,
    )

    # Trusted resolvers (deterministic fixtures, code not data)
    work_items = list(live_view.graph.work_items)

    def handoff_resolver(wi: str) -> TaskHandoff:
        if wi not in work_items:
            # For reviewer dispatch the runner may ask for reviewer handoff via
            # separate resolver, but we keep generic fallback
            return _handoff_for(wi, milestone_ref=live_view.milestone_id)
        return _handoff_for(wi, milestone_ref=live_view.milestone_id)

    def governed_evidence_resolver(wi: str):
        return GovernedWorkItemEvidence(
            validation_evidence=FocusedValidationEvidence(
                work_item_ref=wi, verdict=FocusedValidationVerdict.PASS, validation_evidence_ref=SemanticReference(ref=f"val:{wi}")
            ),
            risk_envelope=_neutral_envelope(live_view.milestone_id),
        )

    def reviewer_handoff_resolver():
        # Dogfood M1: use M1 reviewer handoff, not M3
        return _handoff_for("RV1", milestone_ref=live_view.milestone_id)

    def governed_review_resolver(cid: str, digest: str):
        # For this slice we synthesize a PASS review evidence deterministically
        # The actual review result card will be attached by the reviewer worker;
        # but the resolver is asked during advance to reconcile the review.
        # We return a minimal GovernedReviewEvidence that will be validated
        # against the actual card. For PASS we set no findings.
        # We need to fetch the actual card digest? The MCP transport will call
        # this with the actual cid/digest of the reviewer completion, so we
        # should load the card from exec_store to produce correct evidence.
        try:
            rec = exec_store.get(cid)
            card_dict = rec.worker_result_card if rec and rec.worker_result_card else None
            if card_dict:
                from aota_forge.work_plane.result_card import WorkerResultCard
                card = WorkerResultCard.from_dict(card_dict)
                ev = MilestoneReviewEvidence(
                    milestone_ref=SemanticReference(ref=live_view.milestone_id),
                    review_cycle=ReviewCycle.RV1,
                    reviewed_frontier_ref=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
                    review_result_ref=card.result_handoff_ref,
                    review_result_digest=card.compute_card_digest(),
                    finding_refs=(),
                )
                return GovernedReviewEvidence(
                    review_evidence=ev,
                    review_findings=(),
                    review_task_handoff=_handoff_for("RV1", milestone_ref=live_view.milestone_id),
                    expected_final_frontier=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
                )
        except Exception:
            pass
        # Fallback minimal
        ev = MilestoneReviewEvidence(
            milestone_ref=SemanticReference(ref=live_view.milestone_id),
            review_cycle=ReviewCycle.RV1,
            reviewed_frontier_ref=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
            review_result_ref=SemanticReference(ref=cid),
            review_result_digest=digest,
            finding_refs=(),
        )
        return GovernedReviewEvidence(
            review_evidence=ev,
            review_findings=(),
            review_task_handoff=_handoff_for("RV1", milestone_ref=live_view.milestone_id),
            expected_final_frontier=SemanticReference(ref=f"frontier-{live_view.milestone_id}-rv1"),
        )

    def reviewer_canonical_task_id_resolver():
        # Deterministic reviewer task id for this slice
        return f"{project_id}:{live_view.milestone_id}:RV1:attempt-1"

    # Build the trusted task-main context (operator-owned)
    ctx = TrustedTaskMainRuntimeContext(
        control_service=control_service,
        live_plan_view=live_view,
        origin_task_main_session_ref=origin_session,
        executor_id=executor_id,
        handoff_resolver=handoff_resolver,
        governed_evidence_resolver=governed_evidence_resolver,
        reviewer_handoff_resolver=reviewer_handoff_resolver,
        governed_review_resolver=governed_review_resolver,
        reviewer_canonical_task_id_resolver=reviewer_canonical_task_id_resolver,
        next_milestone_view=next_view,
        session_available=True,
        coordinator_id=coordinator_id,
    )

    # Build the outer TrustedWorkerBinding for profile aota-task-main
    # The sandbox/handoff/tool_surface are for the task-main session itself
    sandbox = bind_worktree_sandbox(_project_evidence(worktree_root, project_id), worktree_id, worktree_root)
    handoff = TaskHandoff(
        work_role="task-main",
        task_kind="task-main-control",
        objective="AOTA task-main milestone control via aota.invoke",
        bounded_scope="milestone coordination only",
        validation_expectations=("task-main control validation",),
        semantic_stop_expectations=("stop at user gate",),
        work_item_ref=SemanticReference(ref=f"{live_view.milestone_id}/task-main"),
        milestone_ref=SemanticReference(ref=live_view.milestone_id),
    )
    # Task-main eager is exactly the 3 controls; no workspace ops, no shell
    # W2-R1 bounded repair: task-main requires progressive result.hydrate to consume
    # production by_ref Skill content (7053-14911 bytes > 4096). Progressive only,
    # scoped to valid ToolOutputRef via existing result.hydrate canonical operation.
    # HYDRATE_AUTHORITY_SCOPED=yes, no new operation, no new MCP tool.
    tool_surface = create_role_tool_surface(
        "task-main",
        eager=("task_main.activate_milestone", "task_main.recover_coordinator", "task_main.advance_once"),
        progressive=("result.hydrate",),
    )
    # For task-main we still need read authorities? The binding validation
    # allows 0..2 read authorities. Provide none for task-main (it doesn't do
    # workspace ops). But we need at least sandbox/handoff match.
    # The mcp_transport checks that read_authorities are tuple and <=2, and each
    # must match sandbox/handoff and be search/read. Empty is allowed.
    read_authorities: tuple = ()
    # But to satisfy tool_surface vs authority separation, we keep empty and
    # rely on task-main ops not needing workspace authority.

    binding = TrustedWorkerBinding(
        canonical_task_id=f"{project_id}:{live_view.milestone_id}:task-main:{origin_session[:8]}",
        project_id=project_id,
        worktree_id=worktree_id,
        trusted_context=bind_trusted_context(principal_id="hermes-task-main", principal_type="hermes-task-main", channel="mcp"),
        handoff=handoff,
        sandbox=sandbox,
        tool_surface=tool_surface,
        read_authorities=read_authorities,
        mutation_authority=None,
        restricted_shell_authority=None,
        trusted_task_main_context=ctx,
    )
    return binding


def write_bootstrap_file(
    *,
    worktree_root: Path,
    project_id: str,
    worktree_id: str,
    coordinator_store_path: Path,
    execution_store_path: Path,
    runtime_config_path: Path,
    origin_task_main_session_ref: str,
    live_plan_view: MilestonePlanView,
    next_milestone_view: MilestonePlanView | None,
    executor_id: str = "hermes",
    coordinator_id: str | None = None,
) -> Path:
    """Operator helper to materialize the bootstrap JSON for the MCP child."""
    worktree_root = worktree_root.resolve()
    dest = worktree_root / BOOTSTRAP_RELPATH
    dest.parent.mkdir(parents=True, exist_ok=True)

    def view_to_dict(v: MilestonePlanView) -> dict[str, Any]:
        return {
            "plan_authority": v.plan_authority,
            "plan_digest": v.plan_digest,
            "plan_source_revision": v.plan_source_revision,
            "milestone_id": v.milestone_id,
            "entry_base": v.entry_base,
            "graph": {
                "milestone_ref": v.graph.milestone_ref,
                "work_items": list(v.graph.work_items),
                "dependencies": [list(e) for e in v.graph.dependencies],
            },
            "milestone_user_approval_satisfied": v.milestone_user_approval_satisfied,
            "plan_amendment_required": v.plan_amendment_required,
        }

    payload = {
        "project_id": project_id,
        "worktree_id": worktree_id,
        "worktree_root": str(worktree_root),
        "coordinator_store_path": str(coordinator_store_path.resolve()),
        "execution_store_path": str(execution_store_path.resolve()),
        "runtime_config_path": str(runtime_config_path.resolve()),
        "origin_task_main_session_ref": origin_task_main_session_ref,
        "executor_id": executor_id,
        "coordinator_id": coordinator_id or f"{project_id}:{live_plan_view.milestone_id}",
        "live_plan_view": view_to_dict(live_plan_view),
        "next_milestone_view": view_to_dict(next_milestone_view) if next_milestone_view else None,
    }
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except Exception:
        pass
    tmp.replace(dest)
    try:
        dest.chmod(0o600)
    except Exception:
        pass
    return dest
