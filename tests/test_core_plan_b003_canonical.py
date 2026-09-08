"""M3 RV2 B003 — Canonical Plan authority contract tests (core/plan).

These tests live at the canonical Plan layer, not in the launcher, so future
launchers cannot reimplement semantics. They cover:

  * root H1 current-state recognition
  * explicit approval yes / no / missing / contradiction
  * generic milestone work items
  * generic DAG (different topologies)
  * missing DAG / ambiguous DAG fails closed
  * non-#37 authority
  * entry base
  * next milestone identity
  * historical/current separation
"""

from __future__ import annotations

import pytest

from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.core.plan.normalize import PlanNormalizationError, normalize_portable_plan
from aota_forge.core.plan.projection import (
    get_entry_base,
    get_milestone_approval,
    get_milestone_graph,
    get_next_milestone_id,
    project_milestone_views,
)


def _body(current: str, *, extra_sections: str = "") -> str:
    return f"# [PLAN] Test Plan\n\n## Current State\n```text\n{current}\n```\n{extra_sections}\n"


class TestRootH1CurrentStateRecognition:
    def test_preamble_is_current(self):
        body = "```text\nPLAN_STATUS=active\nCURRENT_MILESTONE=M3\nM3_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abcdef1234567890\nM3_DAG=W1 -> W2\nM3_WORK_ITEMS=W1, W2\n```\n# Other\n"
        # Preamble before first heading is current
        doc = normalize_portable_plan(body)
        assert doc.plan_status == "active"
        assert doc.current_milestone == "M3"

    def test_h1_with_signature_is_current(self):
        body = """# [AF][OPS] Single-entry Agent Tool Interface & Hermes Operational Activation

```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M3
M3_STATUS=in_progress
M3_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=d116252b17395bc1c1d4f0281340c61d4b16c6aa
M3_DAG=W1 -> W2
M3_WORK_ITEMS=W1, W2
```

## 1. Background
No keys here.
"""
        doc = normalize_portable_plan(body)
        assert doc.plan_status == "active"
        assert doc.current_milestone == "M3"
        assert doc.current_fields["M3_USER_APPROVAL_SATISFIED"] == "yes"

    def test_h1_without_signature_not_current(self):
        body = """# Random Title Without Signature

```text
FOO=bar
STATUS=weird
```

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abc123def4567890
M7_DAG=A -> B
M7_WORK_ITEMS=A, B
```
"""
        doc = normalize_portable_plan(body)
        # Only the Current State section should be authoritative, not the H1
        assert doc.plan_status == "active"
        assert doc.current_milestone == "M7"
        # H1's FOO should be in provenance or ignored, not in current_fields
        assert "FOO" not in doc.current_fields

    def test_arbitrary_unclassified_with_status_not_promoted(self):
        body = """# [PLAN] Test

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abc123def4567890
M7_DAG=A -> B
M7_WORK_ITEMS=A, B
```

## Some Random Section
```text
STATUS=completed
```
"""
        doc = normalize_portable_plan(body)
        assert doc.plan_status == "active"
        # Random section's STATUS should not override
        assert doc.current_status is None or doc.current_status != "completed"


class TestApprovalThreeWay:
    def test_missing_approval_fails_closed(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nENTRY_BASE=abc123def4567890\nM7_DAG=A -> C, B -> C\nM7_WORK_ITEMS=A, B, C"
        )
        doc = normalize_portable_plan(body)
        with pytest.raises(PlanNormalizationError) as exc:
            get_milestone_approval(doc, "M7")
        assert exc.value.diagnostic_code == "MISSING_APPROVAL_TRUTH"

    def test_explicit_no_is_not_approved(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=no\nENTRY_BASE=abc123def4567890\nM7_DAG=A -> C, B -> C\nM7_WORK_ITEMS=A, B, C"
        )
        doc = normalize_portable_plan(body)
        assert get_milestone_approval(doc, "M7") is False

    def test_explicit_yes_is_approved(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_DAG=A -> C, B -> C\nM7_WORK_ITEMS=A, B, C"
        )
        doc = normalize_portable_plan(body)
        assert get_milestone_approval(doc, "M7") is True

    def test_approval_contradiction_fails_closed(self):
        body = """# [PLAN] Test

## Current State A
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abc123def4567890
M7_DAG=A -> B
M7_WORK_ITEMS=A, B
```

## Current State B
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=no
```
"""
        with pytest.raises(PlanNormalizationError) as exc:
            normalize_portable_plan(body)
        assert exc.value.diagnostic_code in ("CURRENT_STATE_CONTRADICTION", "APPROVAL_CONTRADICTION")

    def test_approval_same_value_duplicate_is_info(self):
        body = """# [PLAN] Test

## Current State A
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abc123def4567890
M7_DAG=A -> B
M7_WORK_ITEMS=A, B
```

## Current State B
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=yes
```
"""
        # Same value duplicate should not raise, just diagnostic
        doc = normalize_portable_plan(body)
        assert get_milestone_approval(doc, "M7") is True


class TestGenericWorkItemsAndDAG:
    def test_fixture_a_m7(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_WORK_ITEMS=A, B, C\nM7_DAG=A -> C, B -> C"
        )
        doc = normalize_portable_plan(body)
        graph = get_milestone_graph(doc, "M7")
        assert graph.work_items == ("A", "B", "C")
        assert set(graph.dependencies) == {("A", "C"), ("B", "C")}

    def test_fixture_b_m12(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M12\nM12_STATUS=in_progress\nM12_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM12_WORK_ITEMS=DISCOVER, BUILD, VERIFY, SHIP\nM12_DAG=DISCOVER -> BUILD, BUILD -> VERIFY, VERIFY -> SHIP"
        )
        doc = normalize_portable_plan(body)
        graph = get_milestone_graph(doc, "M12")
        # Sorted canonical
        assert set(graph.work_items) == {"DISCOVER", "BUILD", "VERIFY", "SHIP"}
        assert set(graph.dependencies) == {("DISCOVER", "BUILD"), ("BUILD", "VERIFY"), ("VERIFY", "SHIP")}

    def test_fixture_c_independent(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_WORK_ITEMS=A, B, C\nM7_DAG="
        )
        doc = normalize_portable_plan(body)
        graph = get_milestone_graph(doc, "M7")
        assert set(graph.work_items) == {"A", "B", "C"}
        assert graph.dependencies == ()

    def test_fixture_c_fork_join(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_DAG=W0 -> W1, W0 -> W2, W1 -> W3, W2 -> W3\nM7_WORK_ITEMS=W0, W1, W2, W3"
        )
        doc = normalize_portable_plan(body)
        graph = get_milestone_graph(doc, "M7")
        assert set(graph.work_items) == {"W0", "W1", "W2", "W3"}
        assert set(graph.dependencies) == {("W0", "W1"), ("W0", "W2"), ("W1", "W3"), ("W2", "W3")}

    def test_missing_dag_fails_closed(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_WORK_ITEMS=A, B, C"
        )
        with pytest.raises(PlanNormalizationError) as exc:
            normalize_portable_plan(body)
        assert exc.value.diagnostic_code == "MISSING_DAG_TRUTH"

    def test_ambiguous_dag_fails_closed(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_WORK_ITEMS=A, B, C\nM7_DAG=!!!invalid!!!"
        )
        with pytest.raises(PlanNormalizationError) as exc:
            normalize_portable_plan(body)
        assert exc.value.diagnostic_code in ("AMBIGUOUS_DAG", "MALFORMED_DAG_VALUE", "MISSING_DAG_TRUTH")

    def test_dag_unknown_work_item_fails(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_WORK_ITEMS=A, B, C\nM7_DAG=A -> D"
        )
        with pytest.raises(PlanNormalizationError) as exc:
            normalize_portable_plan(body)
        assert exc.value.diagnostic_code in ("DAG_UNKNOWN_WORK_ITEM", "WORK_ITEMS_DAG_MISMATCH")


class TestNonIssue37Authority:
    def test_generic_authority_via_adapter(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_WORK_ITEMS=ALPHA, BETA, GAMMA\nM7_DAG=ALPHA -> GAMMA, BETA -> GAMMA"
        )
        adapter = StaticPlanAuthorityAdapter(body=body, plan_authority="example-owner/example-governance#123", revision="rev123")
        snap = adapter.load()
        doc = normalize_portable_plan(snap.body, source_revision=snap.revision)
        live, nxt = project_milestone_views(doc, plan_authority=adapter.plan_authority, plan_digest="digest123", plan_source_revision=snap.revision)
        assert live.plan_authority == "example-owner/example-governance#123"
        assert live.milestone_id == "M7"
        assert set(live.graph.work_items) == {"ALPHA", "BETA", "GAMMA"}
        assert set(live.graph.dependencies) == {("ALPHA", "GAMMA"), ("BETA", "GAMMA")}
        assert live.milestone_user_approval_satisfied is True

    def test_second_generic_topology(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M12\nM12_STATUS=in_progress\nM12_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM12_WORK_ITEMS=DISCOVER, BUILD, VERIFY, SHIP\nM12_DAG=DISCOVER -> BUILD, BUILD -> VERIFY, VERIFY -> SHIP"
        )
        adapter = StaticPlanAuthorityAdapter(body=body, plan_authority="other-owner/other-repo#999", revision="rev999")
        snap = adapter.load()
        doc = normalize_portable_plan(snap.body, source_revision=snap.revision)
        live, _ = project_milestone_views(doc, plan_authority=adapter.plan_authority, plan_digest="d", plan_source_revision=snap.revision)
        assert live.plan_authority == "other-owner/other-repo#999"
        assert live.milestone_id == "M12"


class TestEntryBaseAndNextMilestone:
    def test_entry_base_from_plan(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=deadbeef1234567890\nM7_DAG=A -> B\nM7_WORK_ITEMS=A, B"
        )
        doc = normalize_portable_plan(body)
        assert get_entry_base(doc) == "deadbeef1234567890"

    def test_entry_base_missing_fails_closed(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nM7_DAG=A -> B\nM7_WORK_ITEMS=A, B"
        )
        doc = normalize_portable_plan(body)
        with pytest.raises(PlanNormalizationError) as exc:
            get_entry_base(doc)
        assert exc.value.diagnostic_code == "MISSING_ENTRY_BASE"

    def test_next_milestone_from_plan(self):
        body = """# [PLAN] Test

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
M2_STATUS=planned
M2_USER_APPROVAL_SATISFIED=no
ENTRY_BASE=abc123def4567890
M1_WORK_ITEMS=W0, W1
M1_DAG=W0 -> W1
M2_WORK_ITEMS=W2
M2_DAG=
```

## M1 — First
## M2 — Second
"""
        doc = normalize_portable_plan(body)
        assert get_next_milestone_id(doc) == "M2"
        # For M1, next is M2
        adapter = StaticPlanAuthorityAdapter(body=body, plan_authority="owner/repo#1")
        snap = adapter.load()
        doc2 = normalize_portable_plan(snap.body)
        live, nxt = project_milestone_views(doc2, plan_authority="owner/repo#1", plan_digest="d", plan_source_revision="rev")
        assert live.milestone_id == "M1"
        assert nxt is not None and nxt.milestone_id == "M2"
        # For M3 as last, next is None
        body3 = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M3\nM3_STATUS=in_progress\nM3_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM3_DAG=W1 -> W2\nM3_WORK_ITEMS=W1, W2"
        ) + "\n## M3 — Only\n"
        doc3 = normalize_portable_plan(body3)
        assert get_next_milestone_id(doc3) is None

    def test_next_missing_is_none(self):
        body = _body(
            "PLAN_STATUS=active\nCURRENT_MILESTONE=M7\nM7_STATUS=in_progress\nM7_USER_APPROVAL_SATISFIED=yes\nENTRY_BASE=abc123def4567890\nM7_DAG=A -> B\nM7_WORK_ITEMS=A, B"
        ) + "\n## M7 — Only\n"
        doc = normalize_portable_plan(body)
        assert get_next_milestone_id(doc) is None
        adapter = StaticPlanAuthorityAdapter(body=body, plan_authority="owner/repo#1")
        snap = adapter.load()
        doc2 = normalize_portable_plan(snap.body)
        live, nxt = project_milestone_views(doc2, plan_authority="owner/repo#1", plan_digest="d", plan_source_revision="rev")
        assert nxt is None


class TestHistoricalSeparation:
    def test_historical_cannot_override_current(self):
        body = """# [PLAN] Test

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M2
M2_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abc123def4567890
M2_DAG=A -> B
M2_WORK_ITEMS=A, B
```

## M1 Historical Evidence (superseded)
```text
PLAN_STATUS=completed
CURRENT_MILESTONE=M1
M1_USER_APPROVAL_SATISFIED=yes
```
"""
        doc = normalize_portable_plan(body)
        assert doc.plan_status == "active"
        assert doc.current_milestone == "M2"
        assert doc.current_fields["M2_USER_APPROVAL_SATISFIED"] == "yes"
        # Historical values should be in provenance, not current
        assert doc.provenance_observations["M1 Historical Evidence (superseded)"]["CURRENT_MILESTONE"] == "M1"

    def test_current_contradiction_fails_closed(self):
        body = """# [PLAN] Test

## Current A
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=abc123def4567890
M7_DAG=A -> B
M7_WORK_ITEMS=A, B
```

## Current B
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_USER_APPROVAL_SATISFIED=no
```
"""
        with pytest.raises(PlanNormalizationError) as exc:
            normalize_portable_plan(body)
        assert exc.value.diagnostic_code == "CURRENT_STATE_CONTRADICTION"


class TestLauncherConsumption:
    """Launcher must consume canonical typed projection unchanged, not re-parse."""

    def test_launcher_consumes_typed_graph(self):
        # This test ensures launcher does not contain Plan parsing logic
        # It verifies that given a canonical doc, launcher builds bootstrap with exact graph
        import tempfile, json, shutil
        from pathlib import Path
        from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher
        from aota_forge.runtime.task_main.control import TASK_MAIN_PROFILE, WORKER_PROFILE

        tmp = Path(tempfile.mkdtemp())
        worktree = tmp / "wt"
        worktree.mkdir()
        (worktree / ".aota").mkdir()
        hermes_bin = shutil.which("hermes") or "/usr/local/bin/hermes"
        cfg_path = tmp / "runtime.json"
        cfg = {
            "executor": "hermes",
            "executable": hermes_bin,
            "concurrency": 2,
            "provider": "opencode-go",
            "model": "deepseek-v4-flash",
            "bindings": {
                "task-main": {"profile": TASK_MAIN_PROFILE},
                "coder": {"profile": WORKER_PROFILE, "toolsets": ["aota"]},
                "analyst": {"profile": WORKER_PROFILE, "toolsets": ["aota"]},
                "reviewer": {"profile": WORKER_PROFILE, "toolsets": ["aota"]},
                "project-steward": {"profile": WORKER_PROFILE, "toolsets": ["aota"]},
            },
        }
        cfg_path.write_text(json.dumps(cfg))

        body = """# [PLAN] Test

## Current State
```text
PLAN_STATUS=active
CURRENT_MILESTONE=M7
M7_STATUS=in_progress
M7_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE=feedface1234567890
M7_WORK_ITEMS=A, B, C
M7_DAG=A -> C, B -> C
```
"""
        adapter = StaticPlanAuthorityAdapter(body=body, plan_authority="example-owner/example-governance#123", revision="rev1")
        launcher = DailyTaskMainLauncher(plan_adapter=adapter)
        ctx = launcher.prepare(worktree_root=worktree, project_id="aota_forge", worktree_id="test-consume", runtime_config_path=cfg_path)
        # Verify that the graph in bootstrap equals the canonical graph, not a hardcoded one
        bootstrap_path = worktree / ".aota" / "task-main-bootstrap.json"
        data = json.loads(bootstrap_path.read_text())
        assert data["live_plan_view"]["graph"]["work_items"] == ["A", "B", "C"]
        # Dependencies sorted
        assert sorted(data["live_plan_view"]["graph"]["dependencies"]) == [["A", "C"], ["B", "C"]]
        assert data["live_plan_view"]["plan_authority"] == "example-owner/example-governance#123"
        assert data["live_plan_view"]["entry_base"] == "feedface1234567890"
        assert data["live_plan_view"]["milestone_user_approval_satisfied"] is True
        # Cleanup
        shutil.rmtree(tmp, ignore_errors=True)
