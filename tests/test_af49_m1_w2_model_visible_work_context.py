"""AF #49 M1/W2 — Model-visible authoritative Work context (focused V1).

Repairs I40-B004: INTERNAL_WORK_SOURCE_EXISTS != MODEL_VISIBLE_WORK_SOURCE.
The authoritative bounded WorkSourceSlice must reach the task-main model
through the existing task-main control result payload (activate_milestone /
advance_once), before any submit_work_projection, without semantic rewrite.

Proof boundary (honest):
  PROVES=deterministic control-result payload carries the exact structural
         WorkSourceSlice (inline or trusted by-ref) selected mechanically from
         trusted execution state; missing source fails closed with a typed
         insufficient state.
  DOES_NOT_PROVE=real Hermes model visibility (M1 V3), W3 lifecycle,
         W4 handoff grounding.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    try_build_task_main_binding,
    write_bootstrap_file,
)
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.core.plan.read_model import WorkSourceSlice
from aota_forge.core_ingress import (
    CanonicalDispatchBinding,
    _attach_work_context,
)
from aota_forge.mcp_transport import _SharedAotaMcpAdapter
from aota_forge.runtime.task_main.coordinator import (
    WORK_CONTEXT_STATE_AVAILABLE,
    WORK_CONTEXT_STATE_BY_REF,
    WORK_CONTEXT_STATE_INSUFFICIENT,
    MilestonePlanView,
    build_model_visible_work_context,
)
from aota_forge.work_plane.progression import MilestoneWorkItemGraph

PROJECT_ID = "proj_af49w2"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#49"
ENTRY_BASE = "a" * 40

MARKER_ALPHA = "AF49W2_ALPHA_7Q2Z"
MARKER_BETA = "AF49W2_BETA_4M8X"
MARKER_GAMMA = "AF49W2_GAMMA_9K1L"
MARKER_ONLY_W1 = "AF49W2_ONLY_W1_TOKEN_A"
MARKER_ONLY_W2 = "AF49W2_ONLY_W2_TOKEN_B"
MARKER_ONLY_W3 = "AF49W2_ONLY_W3_TOKEN_C"
MARKER_FOREIGN = "AF49W2_FOREIGN_M2_TOKEN"


def _plan_body() -> str:
    return f"""# [PLAN] AF49 W2 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2 -> W3
M1_WORK_ITEMS=W1, W2, W3
M2_DAG=W1 -> W2
M2_WORK_ITEMS=W1, W2
M2_USER_APPROVAL_SATISFIED=no
```

## M1

#### M1/W1 — Authoritative alpha slice
Implement {MARKER_ALPHA} bounded behavior.
Acceptance:
AF49W2_ALPHA_AC=PASS
{MARKER_ONLY_W1}

#### M1/W2 — Authoritative beta slice
Implement {MARKER_BETA} bounded behavior.
Acceptance:
AF49W2_BETA_AC=PASS
{MARKER_ONLY_W2}

#### M1/W3 — Authoritative gamma slice
Implement {MARKER_GAMMA} bounded behavior.
Acceptance:
AF49W2_GAMMA_AC=PASS
{MARKER_ONLY_W3}

## M2

#### M2/W1 — Foreign milestone slice
Implement {MARKER_FOREIGN} behavior.
"""


def _live(marker_body: str | None = None):
    body = marker_body if marker_body is not None else _plan_body()
    doc = normalize_portable_plan(body, source_revision="rev-af49w2")
    live, _next = project_milestone_views(
        doc, plan_authority=PLAN_AUTH, plan_digest="d" * 64, plan_source_revision="rev-af49w2"
    )
    return live


def _make_task_main_binding_for_view(tmp_path: Path, view: MilestonePlanView, session_ref: str = "sess-af49w2"):
    root = tmp_path / f"wt_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        "schema_version: 1\nproject:\n"
        f"  id: {PROJECT_ID}\n  name: t\n  kind: test\n  status: active\n"
        "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
        "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
        "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
        "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
        "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
        "plan:\n  active_plan_id: null\nconstraints: []\n",
        encoding="utf-8",
    )
    coord_path = root / ".aota" / "coord.json"
    exec_path = root / ".aota" / "exec.json"
    coord_path.write_text("{}", encoding="utf-8")
    exec_path.write_text("{}", encoding="utf-8")
    cfg_path = tmp_path / f"runtime_{uuid.uuid4().hex[:6]}.json"
    cfg_path.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": "/bin/false",
                "concurrency": 2,
                "provider": "opencode-go",
                "model": "m",
                "bindings": {
                    "task-main": {"profile": "aota-task-main"},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                },
            }
        ),
        encoding="utf-8",
    )
    write_bootstrap_file(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id="wt-1",
        coordinator_store_path=coord_path,
        execution_store_path=exec_path,
        runtime_config_path=cfg_path,
        origin_task_main_session_ref=session_ref,
        live_plan_view=view,
        next_milestone_view=None,
    )
    old_root = os.environ.get(BOOTSTRAP_ENV_ROOT)
    os.environ[BOOTSTRAP_ENV_ROOT] = str(root)
    try:
        binding = try_build_task_main_binding()
    finally:
        if old_root is None:
            os.environ.pop(BOOTSTRAP_ENV_ROOT, None)
        else:
            os.environ[BOOTSTRAP_ENV_ROOT] = old_root
    assert binding is not None
    return binding, root


def _single_work_view(*, slices: tuple[WorkSourceSlice, ...], work_items: tuple[str, ...] = ("W1",)) -> MilestonePlanView:
    graph = MilestoneWorkItemGraph(milestone_ref="M1", work_items=list(work_items), dependencies=[])
    return MilestonePlanView(
        plan_authority=PLAN_AUTH,
        plan_digest="d" * 64,
        milestone_id="M1",
        entry_base=ENTRY_BASE,
        graph=graph,
        milestone_user_approval_satisfied=True,
        work_source_slices=slices,
    )


# ---------------------------------------------------------------------------
# 1+2. Structural source reaches control result with exact marker fidelity
# ---------------------------------------------------------------------------


def test_activate_exposes_exact_structural_source(tmp_path: Path) -> None:
    live = _live()
    assert {s.work_item_id for s in live.work_source_slices} == {"W1", "W2", "W3"}
    binding, _root = _make_task_main_binding_for_view(tmp_path, live)
    result = _SharedAotaMcpAdapter(binding).invoke("task_main.activate_milestone", {})
    assert result["ok"] is True, result
    payload = result["payload"]
    work_context = payload["work_context"]
    authoritative = live.get_work_source_slice("W1")
    assert authoritative is not None
    assert work_context["state"] == WORK_CONTEXT_STATE_AVAILABLE
    assert work_context["work_item_id"] == "W1"
    assert work_context["milestone_id"] == "M1"
    assert work_context["title"] == authoritative.title
    assert work_context["source_text"] == authoritative.source_text
    assert work_context["plan_ref"] == PLAN_AUTH
    assert work_context["plan_digest"] == live.plan_digest
    assert work_context["work_source_digest"] == hashlib.sha256(
        authoritative.source_text.encode("utf-8")
    ).hexdigest()
    assert work_context["selection"] == "READY"


def test_source_marker_fidelity_no_semantic_rewrite(tmp_path: Path) -> None:
    live = _live()
    binding, _root = _make_task_main_binding_for_view(tmp_path, live)
    adapter = _SharedAotaMcpAdapter(binding)
    assert adapter.invoke("task_main.activate_milestone", {})["ok"] is True
    result = adapter.invoke("task_main.recover_coordinator", {})
    assert result["ok"] is True, result
    source_text = result["payload"]["work_context"]["source_text"]
    for marker in (MARKER_ALPHA, MARKER_ONLY_W1, "AF49W2_ALPHA_AC=PASS", "M1/W1 — Authoritative alpha slice"):
        assert marker in source_text
    # No generated summary / generic objective substitutes for the source.
    assert "Implement W1" not in source_text
    assert "generic objective" not in source_text.lower()


# ---------------------------------------------------------------------------
# 3+4. Mechanical selection, no sibling leak, no cross-milestone leak
# ---------------------------------------------------------------------------


def test_mechanical_selection_from_execution_state() -> None:
    live = _live()
    # Initial: only the DAG root (W1) is ready; W2 (blocked by dependency) must not be selected.
    ready_ctx = build_model_visible_work_context(live, wi_status={"W1": "PENDING", "W2": "PENDING", "W3": "PENDING"})
    assert ready_ctx is not None
    assert ready_ctx["work_item_id"] == "W1" and ready_ctx["selection"] == "READY"
    assert MARKER_ONLY_W2 not in ready_ctx["source_text"]
    assert MARKER_ONLY_W3 not in ready_ctx["source_text"]
    # In-flight wins when nothing is ready.
    inflight = build_model_visible_work_context(live, wi_status={"W1": "ACTIVE", "W2": "PENDING", "W3": "PENDING"})
    assert inflight is not None
    assert inflight["work_item_id"] == "W1" and inflight["selection"] == "IN_FLIGHT"
    # Completed predecessor advances readiness to W2 without sibling/foreign leakage.
    advanced = build_model_visible_work_context(
        live,
        wi_status={"W1": "COMPLETION_PENDING_RECONCILIATION", "W2": "PENDING", "W3": "PENDING"},
        wi_semantic_status={"W1": "RECONCILED"},
    )
    assert advanced is not None
    assert advanced["work_item_id"] == "W2" and advanced["selection"] == "READY"
    assert MARKER_ONLY_W1 not in advanced["source_text"]
    assert MARKER_ONLY_W3 not in advanced["source_text"]
    assert MARKER_FOREIGN not in advanced["source_text"]
    # Completion pending (nothing ready) selects the pending reconciliation Work.
    pending = build_model_visible_work_context(
        live,
        wi_status={
            "W1": "COMPLETION_PENDING_RECONCILIATION",
            "W2": "COMPLETION_PENDING_RECONCILIATION",
            "W3": "COMPLETION_PENDING_RECONCILIATION",
        },
        wi_semantic_status={"W2": "RECONCILED", "W3": "RECONCILED"},
    )
    assert pending is not None
    assert pending["work_item_id"] == "W1"
    assert pending["selection"] == "COMPLETION_PENDING_RECONCILIATION"
    # Fully reconciled: no Work needs semantic reasoning.
    complete = build_model_visible_work_context(
        live,
        wi_status={
            "W1": "COMPLETION_PENDING_RECONCILIATION",
            "W2": "COMPLETION_PENDING_RECONCILIATION",
            "W3": "COMPLETION_PENDING_RECONCILIATION",
        },
        wi_semantic_status={wid: "RECONCILED" for wid in ("W1", "W2", "W3")},
    )
    assert complete is None


def test_sibling_and_cross_milestone_source_do_not_leak() -> None:
    live = _live()
    w1 = live.get_work_source_slice("W1")
    w2 = live.get_work_source_slice("W2")
    assert w1 is not None and w2 is not None
    assert MARKER_ONLY_W2 not in w1.source_text and MARKER_ONLY_W3 not in w1.source_text
    assert MARKER_ONLY_W1 not in w2.source_text and MARKER_ONLY_W3 not in w2.source_text
    assert MARKER_FOREIGN not in w1.source_text
    # Cross-milestone slice cannot be attached to this Milestone view (fail closed).
    foreign = WorkSourceSlice(
        milestone_id="M2", work_item_id="W1", title="M2/W1 — foreign", source_text=MARKER_FOREIGN
    )
    with pytest.raises(ValueError):
        _single_work_view(slices=(foreign,))


# ---------------------------------------------------------------------------
# 5. Missing structural source fails closed (no fallback)
# ---------------------------------------------------------------------------


def test_missing_source_fails_closed_without_fallback() -> None:
    graph = MilestoneWorkItemGraph(milestone_ref="M1", work_items=["W1"], dependencies=[])
    view = _single_work_view(slices=())
    context = build_model_visible_work_context(view)
    assert context is not None
    assert context["state"] == WORK_CONTEXT_STATE_INSUFFICIENT
    assert context["code"] == "MISSING_WORK_SOURCE"
    assert context["work_item_id"] == "W1"
    assert "source_text" not in context
    assert "README" in context["reason"] and "fallback" in context["reason"]
    # Unknown Work identity can never be selected from a trusted graph.
    assert view.get_work_source_slice("W9") is None
    assert graph.work_items == ("W1",)


def test_single_work_missing_source_through_model_visible_seam(tmp_path: Path) -> None:
    view = _single_work_view(slices=())
    binding, _root = _make_task_main_binding_for_view(tmp_path, view)
    adapter = _SharedAotaMcpAdapter(binding)
    activated = adapter.invoke("task_main.activate_milestone", {})
    assert activated["ok"] is True, activated
    context = activated["payload"]["work_context"]
    assert context["state"] == WORK_CONTEXT_STATE_INSUFFICIENT
    assert context["code"] == "MISSING_WORK_SOURCE"
    # The fail-closed advance keeps the explicit typed insufficiency, never a generic scope.
    advanced = adapter.invoke("task_main.advance_once", {})
    assert advanced["ok"] is False
    assert advanced["error"]["work_context"]["state"] == WORK_CONTEXT_STATE_INSUFFICIENT
    assert advanced["error"]["work_context"]["code"] == "MISSING_WORK_SOURCE"
    assert "source_text" not in advanced["error"]["work_context"]


# ---------------------------------------------------------------------------
# 6. Source visible before submit_work_projection
# ---------------------------------------------------------------------------


def test_source_visible_without_submit_work_projection(tmp_path: Path) -> None:
    live = _live()
    binding, _root = _make_task_main_binding_for_view(tmp_path, live)
    adapter = _SharedAotaMcpAdapter(binding)
    activated = adapter.invoke("task_main.activate_milestone", {})
    assert activated["ok"] is True, activated
    context = activated["payload"]["work_context"]
    assert context["state"] == WORK_CONTEXT_STATE_AVAILABLE
    assert context["source_text"] == live.get_work_source_slice("W1").source_text
    # No semantic projection was committed; the advance failure still carries the
    # exact authoritative source so semantic reasoning is possible.
    advanced = adapter.invoke("task_main.advance_once", {})
    assert advanced["ok"] is False
    assert advanced["error"]["code"] == "WORK_SCOPE_INSUFFICIENT"
    failure_context = advanced["error"]["work_context"]
    if failure_context["state"] == WORK_CONTEXT_STATE_AVAILABLE:
        assert failure_context["source_text"] == live.get_work_source_slice("W1").source_text
    else:
        assert failure_context["state"] == WORK_CONTEXT_STATE_BY_REF
        assert failure_context["work_source_digest"] == hashlib.sha256(
            live.get_work_source_slice("W1").source_text.encode("utf-8")
        ).hexdigest()


# ---------------------------------------------------------------------------
# 7. role.bootstrap remains bootstrap/context — not dynamic Work delivery
# ---------------------------------------------------------------------------


def test_role_bootstrap_is_not_dynamic_work_source_delivery(tmp_path: Path) -> None:
    live = _live()
    binding, _root = _make_task_main_binding_for_view(tmp_path, live)
    adapter = _SharedAotaMcpAdapter(binding)
    boot = adapter.invoke("role.bootstrap", {})
    assert boot["ok"] is True, boot
    # W5: over-bound bootstrap is a governed by_ref result; consume it via its
    # model-visible hydration claims (existing result.hydrate).
    if boot["output_mode"] == "inline":
        payload = boot["payload"]
    else:
        hydration = boot["hydration"]
        assert hydration["operation"] == "result.hydrate"
        hydrated = adapter.invoke("result.hydrate", dict(hydration["arguments"]))
        assert hydrated["ok"] is True, hydrated
        payload = json.loads(hydrated["payload"]["content"])
    assert "work_context" not in payload
    serialized = json.dumps(payload)
    assert MARKER_ALPHA not in serialized
    assert MARKER_BETA not in serialized


# ---------------------------------------------------------------------------
# 8+9. Bounds: inline when it fits, trusted by-ref round-trip when it does not
# ---------------------------------------------------------------------------


def test_inline_bound_respected_and_source_exact(tmp_path: Path) -> None:
    live = _live()
    binding, _root = _make_task_main_binding_for_view(tmp_path, live)
    result = _SharedAotaMcpAdapter(binding).invoke("task_main.activate_milestone", {})
    assert result["ok"] is True, result
    if result["output_mode"] == "inline":
        assert result["payload"]["work_context"]["source_text"] == live.get_work_source_slice("W1").source_text
    else:
        assert result["output_mode"] == "by_ref"
        assert result["output_ref"] is not None


def test_oversized_source_returns_trusted_by_ref_and_roundtrips(tmp_path: Path) -> None:
    big_source = "M1/W1 — Oversized authoritative slice\n" + (
        f"{MARKER_ALPHA} " + "X" * 6000
    )
    view = _single_work_view(
        slices=(WorkSourceSlice(milestone_id="M1", work_item_id="W1", title="M1/W1 — Oversized", source_text=big_source),)
    )
    binding, _root = _make_task_main_binding_for_view(tmp_path, view)
    adapter = _SharedAotaMcpAdapter(binding)
    result = adapter.invoke("task_main.activate_milestone", {})
    assert result["ok"] is True, result
    context = result["payload"]["work_context"]
    assert context["state"] == WORK_CONTEXT_STATE_BY_REF
    assert "source_text" not in context
    assert context["work_item_id"] == "W1"
    assert context["milestone_id"] == "M1"
    assert context["plan_ref"] == PLAN_AUTH
    assert context["plan_digest"] == "d" * 64
    source_ref = context["source_ref"]
    assert source_ref["digest"] == context["work_source_digest"]
    # Exact source round-trips through the existing governed hydration operation.
    hydrated = adapter.invoke(
        "result.hydrate",
        {
            "ref": source_ref["ref"],
            "digest": source_ref["digest"],
            "project_id": source_ref["project_id"],
            "worktree_id": source_ref["worktree_id"],
            "byte_length": source_ref["byte_length"],
        },
    )
    assert hydrated["ok"] is True, hydrated
    assert hydrated["payload"]["content"] == big_source
    assert MARKER_ALPHA in hydrated["payload"]["content"]
    # No silent truncation anywhere in the chain.
    assert len(hydrated["payload"]["content"].encode("utf-8")) == source_ref["byte_length"]


def test_unrepresentable_source_never_silently_truncates() -> None:
    binding = CanonicalDispatchBinding()
    payload: dict = {}
    context = {
        "state": WORK_CONTEXT_STATE_AVAILABLE,
        "work_item_id": "W1",
        "milestone_id": "M1",
        "title": "M1/W1",
        "source_text": "Y" * 4096,
        "plan_ref": PLAN_AUTH,
        "plan_digest": "d" * 64,
        "work_source_digest": "e" * 64,
        "selection": "READY",
    }
    _attach_work_context(binding, payload, context, operation="task_main.advance_once", inline_bound=32)
    assert payload["work_context"]["state"] == WORK_CONTEXT_STATE_INSUFFICIENT
    assert payload["work_context"]["code"] == "WORK_SOURCE_UNREPRESENTABLE"
    assert "source_text" not in payload["work_context"]


# ---------------------------------------------------------------------------
# 10. Negative probes: trusted identity is never model-selected
# ---------------------------------------------------------------------------


def test_model_cannot_supply_trusted_source_identity(tmp_path: Path) -> None:
    live = _live()
    binding, _root = _make_task_main_binding_for_view(tmp_path, live)
    result = _SharedAotaMcpAdapter(binding).invoke("task_main.activate_milestone", {"work_item_id": "W2"})
    assert result["ok"] is False
    assert result["error"]["code"] in ("UNKNOWN_INPUT", "INPUT_TYPE_INVALID")


def test_mismatched_plan_digest_is_visible_and_digest_bound() -> None:
    live = _live()
    context = build_model_visible_work_context(live)
    assert context is not None
    assert context["plan_digest"] == live.plan_digest
    source_text = context["source_text"]
    assert context["work_source_digest"] == hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    tampered = source_text.replace(MARKER_ALPHA, "AF49W2_TAMPERED")
    assert hashlib.sha256(tampered.encode("utf-8")).hexdigest() != context["work_source_digest"]


def test_foreign_and_unknown_work_source_fail_closed() -> None:
    live = _live()
    assert live.get_work_source_slice("W9") is None
    foreign = WorkSourceSlice(
        milestone_id="M2", work_item_id="W1", title="M2/W1 — foreign", source_text=MARKER_FOREIGN
    )
    with pytest.raises(ValueError):
        _single_work_view(slices=(foreign,))
    # Trusted selection W1 (ready) has no slice; a sibling W2 slice must not substitute.
    sibling_only = WorkSourceSlice(
        milestone_id="M1", work_item_id="W2", title="M1/W2 — sibling", source_text=MARKER_ONLY_W2
    )
    view = _single_work_view(slices=(sibling_only,), work_items=("W1", "W2"))
    context = build_model_visible_work_context(view)
    assert context is not None
    assert context["work_item_id"] == "W1"
    assert context["state"] == WORK_CONTEXT_STATE_INSUFFICIENT
    assert context["code"] == "MISSING_WORK_SOURCE"
    assert "source_text" not in context
