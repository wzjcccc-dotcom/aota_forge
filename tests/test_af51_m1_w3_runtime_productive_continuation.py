"""AF #51 M1/W3 — runtime-owned bounded productive continuation after completion.

I40-B007 progression convergence. The accepted runtime-owned completion
continuation delivers a Worker completion CARD into the exact parent session
and the Agent acknowledges it; that delivery turn is ACK-only. Without a
further turn the Agent can never reconcile the completion into the milestone
coordinator and dispatch the next governed Work Item (the #40 diagnostic:
"no autonomous progression turn exists after the first completion ACK"; the
operator had to invoke ``DailyTaskMainLauncher.resume()`` manually).

W3 convergence: inside the ONE operator launch, when (and only when) a bounded
runtime-owned completion pass actually delivered an acknowledged completion,
the launcher continues the SAME exact session with the operator-owned startup
prompt so the task-main Agent can continue its normal cycle. It is bounded,
runtime-owned, and makes no semantic decision:

  PRODUCTIVE_CONTINUATION_MODEL_OWNS_SEMANTICS=yes
  PRODUCTIVE_CONTINUATION_IS_MANUAL_WAKEUP=no
  PRODUCTIVE_CONTINUATION_BOUNDED=yes
  PRODUCTIVE_CONTINUATION_CALLS_SEMANTIC_CONTROLS=no

Proof boundary (honest):
  PROVES=deterministic component integration: completion pass -> exact-session
         continuation -> bounded stop; no delivery => no turn; no semantic
         control call by the launcher.
  DOES_NOT_PROVE=real Hermes model/Worker, real parent re-entry, real
         production V3 (the W3 V3 run owns that).
"""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.hermes.session_reentry import (
    OUTCOME_COMPLETED,
    HermesReentryResult,
)
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.composition.task_main_daily_launcher import (
    DEFAULT_MAX_PRODUCTIVE_CONTINUATIONS,
    PRODUCTIVE_CONTINUATION_BOUNDED,
    PRODUCTIVE_CONTINUATION_CALLS_SEMANTIC_CONTROLS,
    PRODUCTIVE_CONTINUATION_IS_MANUAL_WAKEUP,
    PRODUCTIVE_CONTINUATION_MODEL_OWNS_SEMANTICS,
    DailyTaskMainLauncher,
)
from aota_forge.composition.task_main_host_bootstrap import (
    BOOTSTRAP_ENV_ROOT,
    BOOTSTRAP_RELPATH,
)
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.runtime.completion import (
    DELIVER_ACKNOWLEDGED,
    DELIVER_DROPPED_SESSION_MISSING,
    DELIVER_RELEASED_ACK_NOT_PROVEN,
)

PROJECT_ID = "aota_forge_w3_fixture"
PLAN_AUTH = "wzjcccc-dotcom/aota-hermes-tools#51"
ENTRY_BASE = "a" * 40
REAL_SESSION = "20260913_120000_w3a51"


def _plan_body() -> str:
    return f"""# [PLAN] AF51 W3 fixture

## Current State
```text
PLAN_TYPE=portable_plan
PLAN_STATUS=active
CURRENT_MILESTONE=M1
M1_STATUS=in_progress
M1_USER_APPROVAL_SATISFIED=yes
ENTRY_BASE={ENTRY_BASE}
M1_DAG=W1 -> W2
M1_WORK_ITEMS=W1, W2
```

## M1

#### M1/W1 — Authoritative alpha slice
Create the bounded W3 alpha artifact.

#### M1/W2 — Authoritative beta slice
Create the bounded W3 beta artifact.
"""


def _runtime_config_json() -> dict:
    return {
        "executor": "hermes",
        "executable": "/bin/false",
        "concurrency": 1,
        "provider": "aota-test-provider",
        "model": "aota-test-model",
        "bindings": {
            "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
            "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
            "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
            "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
            "task-main": {"profile": "aota-task-main"},
        },
    }


def _report(outcomes: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(
        delivery_outcomes=outcomes,
        stop_reason="no_relevant_active_execution",
    )


class _RecordingLauncher(DailyTaskMainLauncher):
    """Launcher with scripted exact-session continuation and completion passes."""

    def __init__(self, *, reentry_payloads: list[str]) -> None:
        super().__init__(plan_adapter=None, hermes_bin="/bin/false")
        self._reentry_payloads = reentry_payloads
        self.continuation_calls = 0
        self.scripted_reports: list[SimpleNamespace] = []
        self.continued: list[dict] = []

    def _continue_exact_session(self, *, ctx, session_id, payload, timeout_seconds=120, trace_path=None):
        self.continued.append(
            {"session_id": session_id, "payload": payload, "ctx_project": ctx.project_id}
        )
        return object()

    def _run_autonomous_completion_continuation(
        self, *, ctx, session_id, timeout_seconds=None, trace_path=None
    ):
        index = self.continuation_calls
        self.continuation_calls += 1
        if index < len(self.scripted_reports):
            return self.scripted_reports[index]
        return _report(())


class TestBoundedProductiveContinuation:
    def test_acknowledged_delivery_grants_one_bounded_productive_turn(self) -> None:
        launcher = _RecordingLauncher(reentry_payloads=[])
        launcher.scripted_reports = [
            _report(()),
            _report(()),
        ]
        ctx = SimpleNamespace(project_id=PROJECT_ID)
        turns = launcher._run_bounded_productive_continuations(
            ctx=ctx,
            session_id=REAL_SESSION,
            continuation=_report((DELIVER_ACKNOWLEDGED,)),
            startup_prompt="OPERATOR STARTUP PROMPT",
            timeout_seconds=30,
            completion_timeout_seconds=60,
            max_productive_continuations=DEFAULT_MAX_PRODUCTIVE_CONTINUATIONS,
            trace_path=None,
        )
        assert turns == 1
        assert len(launcher.continued) == 1
        assert launcher.continued[0]["session_id"] == REAL_SESSION
        assert launcher.continued[0]["payload"] == "OPERATOR STARTUP PROMPT"

    def test_no_delivery_no_productive_turn(self) -> None:
        launcher = _RecordingLauncher(reentry_payloads=[])
        for outcome_set in ((), (DELIVER_RELEASED_ACK_NOT_PROVEN,), (DELIVER_DROPPED_SESSION_MISSING,)):
            launcher.continued.clear()
            launcher.continuation_calls = 0
            launcher.scripted_reports = [_report(outcome_set)]
            turns = launcher._run_bounded_productive_continuations(
                ctx=SimpleNamespace(project_id=PROJECT_ID),
                session_id=REAL_SESSION,
                continuation=_report(outcome_set),
                startup_prompt="OPERATOR STARTUP PROMPT",
                timeout_seconds=30,
                completion_timeout_seconds=60,
                max_productive_continuations=DEFAULT_MAX_PRODUCTIVE_CONTINUATIONS,
                trace_path=None,
            )
            assert turns == 0
            assert launcher.continued == []

    def test_productive_continuation_is_strictly_bounded(self) -> None:
        launcher = _RecordingLauncher(reentry_payloads=[])
        # Every completion pass delivers an acknowledged completion: the loop
        # must stop exactly at the explicit bound, never forever.
        launcher.scripted_reports = [_report((DELIVER_ACKNOWLEDGED,)) for _ in range(10)]
        turns = launcher._run_bounded_productive_continuations(
            ctx=SimpleNamespace(project_id=PROJECT_ID),
            session_id=REAL_SESSION,
            continuation=_report((DELIVER_ACKNOWLEDGED,)),
            startup_prompt="OPERATOR STARTUP PROMPT",
            timeout_seconds=30,
            completion_timeout_seconds=60,
            max_productive_continuations=2,
            trace_path=None,
        )
        assert turns == 2
        assert len(launcher.continued) == 2

    def test_invalid_bound_fails_closed(self) -> None:
        launcher = _RecordingLauncher(reentry_payloads=[])
        with pytest.raises(ValueError):
            launcher._run_bounded_productive_continuations(
                ctx=SimpleNamespace(project_id=PROJECT_ID),
                session_id=REAL_SESSION,
                continuation=_report(()),
                startup_prompt="OPERATOR STARTUP PROMPT",
                timeout_seconds=30,
                completion_timeout_seconds=60,
                max_productive_continuations=-1,
                trace_path=None,
            )

    def test_launcher_never_calls_semantic_controls(self) -> None:
        source = inspect.getsource(DailyTaskMainLauncher._run_bounded_productive_continuations)
        for forbidden_call in (
            "advance_once(",
            "advance_milestone_once(",
            "compare_and_swap(",
            "activate_milestone(",
            "submit_work_projection(",
        ):
            assert forbidden_call not in source
        assert "coordinator_store" not in source
        assert PRODUCTIVE_CONTINUATION_MODEL_OWNS_SEMANTICS is True
        assert PRODUCTIVE_CONTINUATION_IS_MANUAL_WAKEUP is False
        assert PRODUCTIVE_CONTINUATION_BOUNDED is True
        assert PRODUCTIVE_CONTINUATION_CALLS_SEMANTIC_CONTROLS is False
        assert DEFAULT_MAX_PRODUCTIVE_CONTINUATIONS == 3


class TestLaunchIntegration:
    def test_launch_runs_bounded_productive_turn_without_manual_resume(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        root = tmp_path / "wt_w3_productive"
        root.mkdir(parents=True, exist_ok=True)
        cfg = tmp_path / "runtime_w3_productive.json"
        cfg.write_text(json.dumps(_runtime_config_json()), encoding="utf-8")
        adapter = StaticPlanAuthorityAdapter(
            body=_plan_body(), plan_authority=PLAN_AUTH, revision="rev-af51w3"
        )
        launcher = DailyTaskMainLauncher(plan_adapter=adapter, hermes_bin="/bin/false")

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd, capture_output=False, text=True, timeout=None, env=None, **kwargs):
            subprocess_calls.append(list(cmd))
            usage_path = Path(cmd[cmd.index("--usage-file") + 1])
            usage_path.write_text(
                json.dumps({"session_id": REAL_SESSION, "completed": True}), encoding="utf-8"
            )
            import subprocess as _subprocess

            return _subprocess.CompletedProcess(cmd, 0, stdout="session ready", stderr="")

        import aota_forge.composition.task_main_daily_launcher as launcher_mod

        monkeypatch.setattr(launcher_mod.subprocess, "run", fake_run)

        reentry_calls: list[dict] = []

        class RecordingReentry:
            def reenter(self, session_id, payload):
                reentry_calls.append({"session_id": session_id, "payload": payload})
                return HermesReentryResult(
                    outcome=OUTCOME_COMPLETED,
                    retryable=False,
                    exact_session_found=True,
                    turn_accepted=True,
                    busy=False,
                    exit_code=0,
                    session_id=session_id,
                    resolved_session_id=session_id,
                    error_code=None,
                    error_message=None,
                    response_excerpt="continued",
                    stderr_excerpt=None,
                )

        monkeypatch.setattr(
            launcher,
            "_build_exact_session_reentry",
            lambda *, ctx, timeout_seconds: RecordingReentry(),
        )
        from aota_forge.adapters.hermes.session_reentry import (
            PersistedSessionToolSurface,
        )
        from aota_forge.composition.task_main_daily_launcher import AfMcpSurfaceProbe

        monkeypatch.setattr(
            launcher,
            "_observe_exact_session_tool_surface",
            lambda *, session_id: PersistedSessionToolSurface(
                True, ("tool_search", "tool_describe", "tool_call"), None
            ),
        )
        monkeypatch.setattr(
            launcher,
            "_probe_af_mcp_tool_surface",
            lambda *, ctx, env=None: AfMcpSurfaceProbe(True, ("aota.invoke",), None),
        )

        passes: list[dict] = []

        def fake_completion_continuation(*, ctx, session_id, timeout_seconds=None, trace_path=None):
            passes.append({"session_id": session_id, "timeout_seconds": timeout_seconds})
            if len(passes) == 1:
                return _report((DELIVER_ACKNOWLEDGED,))
            return _report(())

        monkeypatch.setattr(
            launcher, "_run_autonomous_completion_continuation", fake_completion_continuation
        )

        ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id="wt-w3-productive",
            runtime_config_path=cfg,
            plan_adapter=adapter,
            initial_prompt="W3 PHASE2 OPERATOR STARTUP KICKOFF",
            timeout_seconds=30,
            max_productive_continuations=1,
        )

        assert session_id == REAL_SESSION
        assert len(subprocess_calls) == 1, "phase 1 must be the only session creation call"
        # PHASE 2 + exactly one runtime-owned productive continuation turn.
        assert len(reentry_calls) == 2
        assert all(call["session_id"] == REAL_SESSION for call in reentry_calls)
        assert reentry_calls[0]["payload"] == "W3 PHASE2 OPERATOR STARTUP KICKOFF"
        assert reentry_calls[1]["payload"] == "W3 PHASE2 OPERATOR STARTUP KICKOFF"
        assert len(passes) == 2
        assert ctx.worktree_root == root.resolve()
        assert json.loads((root / BOOTSTRAP_RELPATH).read_text(encoding="utf-8"))[
            "origin_task_main_session_ref"
        ] == REAL_SESSION
        assert os.environ.get(BOOTSTRAP_ENV_ROOT) is None
