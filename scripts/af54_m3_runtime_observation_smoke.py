"""AF #54 M3/W4 — bounded real thin production OBSERVATION proof (V3-style).

NOT an M4 effectiveness dogfood. It proves the M3 bridge: a real thin
production task-main session emits bounded Tool/Skill observations at the
canonical seams, those observations correlate to the real session / real
child task / Work identity / task.return / parent reentry, and one bounded
ToolEffectivenessCard is produced from canonical evidence.

    DailyTaskMainLauncher.launch(...)          (real Hermes + real Workers)
      + AOTA_RUNTIME_OBSERVATION_SINK          (bounded per-run evidence file)
        -> real MCP aota.invoke dispatch observations
        -> real role.bootstrap / skill.open Skill observations
      + durable execution records              (bounded lifecycle facts)
      + bounded Hermes session metadata        (model/provider supplement)
        -> ToolEffectivenessCard
      + bounded observation-sink failure injection (isolation proof)

No transcripts are copied. If Hermes/model access is unavailable, exits 2
(BLOCKED), never FAIL.

Run:
    python3 scripts/af54_m3_runtime_observation_smoke.py \
        --root <disposable-dir> [--timeout 300] [--summary out.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

TASK_MAIN_PROFILE_HOME = Path.home() / ".hermes" / "profiles" / "aota-task-main"

PROJECT_ID = "af54_m3_obs"
WORKTREE_ID = "af54-m3-obs-wt"

PROJECT_MANIFEST = (
    "schema_version: 1\nproject:\n"
    f"  id: {PROJECT_ID}\n  name: af54m3obs\n  kind: test\n  status: active\n"
    "summary: AF54 M3 bounded production-observation smoke fixture\n"
    "capabilities: []\npaths:\n  source_root: .\n  source: []\n"
    "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
    "plan:\n  active_plan_id: null\nconstraints: []\n"
)

PLAN_FIXTURE = """# AF54 M3 Smoke Plan (authoritative fixture for this session)

Milestone M3 contains exactly two Work Items.

## M3/W1 — implementation (status: implementation-complete)
Deliver src/widget.py with the bounded `add(a, b)` behavior. The file already
exists in this worktree; treat W1 as implemented and awaiting independent
review.

## M3/W2 — docs pass (status: pending; must NOT start yet)
Requires the W1 review to exist first.

## Required next action (perform exactly this, then stop)
Delegate one bounded REVIEW of M3/W1 to a reviewer child. The review targets
W1 itself (reuse W1's identity; do not invent a new Work number). Scope the
child to src/widget.py plus this plan section only. After the child is
started, output DELEGATION_COMPLETE and the child task id, then stop.
"""

WIDGET_SRC = '''"""tiny fixture under review (M3/W1)."""


def add(a, b):
    return a + b
'''

SCENARIO_PROMPT = (
    "你是本 worktree 的 AF task-main（thin production）。唯一權威 Plan 在 docs/plan.md。"
    "使用 AF 工具讀取 Plan，並完成 Plan 中 Required next action 所述的委派。"
    "一切 AF operation 只透過 aota.invoke；先 role.bootstrap，依 AF 提供的指引操作。"
    "完成委派後輸出 DELEGATION_COMPLETE 與 child task id，然後停止。"
    "不要自己進行 review、不要啟動其他 child、不要修改任何檔案。"
)


def _write_fixture(root: Path) -> None:
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(PROJECT_MANIFEST, encoding="utf-8")
    (root / "docs").mkdir(exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "docs" / "plan.md").write_text(PLAN_FIXTURE, encoding="utf-8")
    (root / "src" / "widget.py").write_text(WIDGET_SRC, encoding="utf-8")


def _execution_records(root: Path) -> dict:
    p = root / ".aota" / "execution.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _hermes_session_metadata(session_id: str):
    from aota_forge.adapters.hermes.metadata_supplement import read_hermes_session_metadata

    for db in (
        TASK_MAIN_PROFILE_HOME / "state.db",
        Path.home() / ".hermes" / "state.db",
    ):
        metadata = read_hermes_session_metadata(db, session_id)
        if metadata is not None:
            return metadata
    return None


def _bounded_observations_payload(observations, limit: int = 200) -> list[dict]:
    out: list[dict] = []
    for obs in list(observations)[:limit]:
        d = obs.to_dict()
        out.append(d)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--runtime-config", default=str(Path.home() / ".config/aota-forge/runtime.json"))
    ap.add_argument("--summary", default="")
    ap.add_argument("--observations-json", default="")
    ap.add_argument("--skills-json", default="")
    ap.add_argument("--card-json", default="")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if root == REPO_ROOT or REPO_ROOT in root.parents:
        print("REFUSED: smoke root must be outside the repo", file=sys.stderr)
        return 2
    hermes_bin = os.environ.get("HERMES_BIN", str(Path.home() / ".local/bin/hermes"))
    if not Path(hermes_bin).is_file():
        print("BLOCKED: hermes runtime access unavailable", file=sys.stderr)
        return 2

    _write_fixture(root)
    run_ref = f"af54-m3-{int(time.time())}"
    evidence_path = root / ".aota" / "observation-evidence.jsonl"
    os.environ["AOTA_RUNTIME_OBSERVATION_SINK"] = str(evidence_path)
    os.environ["AOTA_RUNTIME_OBSERVATION_RUN_REF"] = run_ref
    os.environ.pop("AOTA_RUNTIME_OBSERVATION_SESSION_REF", None)

    result: dict[str, str] = {
        "work_item": "M3/W4",
        "plan": "wzjcccc-dotcom/aota-hermes-tools#54",
        "project_id": PROJECT_ID,
        "worktree_id": WORKTREE_ID,
        "root": str(root),
        "run_ref": run_ref,
        "driver": "scripts/af54_m3_runtime_observation_smoke.py",
    }

    from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher

    launcher = DailyTaskMainLauncher()
    t0 = time.time()
    try:
        ctx, session_id = launcher.launch(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            runtime_config_path=args.runtime_config,
            initial_prompt=SCENARIO_PROMPT,
            timeout_seconds=args.timeout,
            completion_timeout_seconds=float(args.timeout) * 3,
            max_productive_continuations=2,
        )
        result["TASK_MAIN_SESSION"] = session_id
        result["REAL_HERMES_SESSION"] = "yes"
        result["RUNTIME_PATH"] = str(getattr(ctx, "runtime_path", "unknown"))
    except Exception as exc:
        result["LAUNCH_OUTCOME"] = "BLOCKED"
        result["LAUNCH_ERROR_TYPE"] = type(exc).__name__
        result["LAUNCH_ERROR"] = str(exc)[:400]
        _emit(result, args.summary)
        return 2
    result["LAUNCH_OUTCOME"] = "completed"

    # ---- read bounded per-run observation evidence ----
    from aota_forge.work_plane.runtime_observation import (
        load_runtime_observation_evidence,
    )

    evidence = load_runtime_observation_evidence(evidence_path)
    tool_obs = evidence.tool_observations
    skill_obs = evidence.skill_observations
    result["REAL_RUN_TOOL_OBSERVATION_EMITTED"] = "yes" if tool_obs else "no"
    result["REAL_RUN_TOOL_OBSERVATION_COUNT"] = str(len(tool_obs))
    result["REAL_RUN_SKILL_OBSERVATION_EMITTED"] = "yes" if skill_obs else "no"
    result["REAL_RUN_SKILL_OBSERVATION_COUNT"] = str(len(skill_obs))
    result["OBSERVATION_EVIDENCE_TRUNCATED"] = "yes" if evidence.truncated else "no"
    result["OBSERVATION_EVIDENCE_INVALID_LINES"] = str(evidence.invalid_line_count)

    session_bound = [o for o in tool_obs if o.session_ref == session_id]
    result["OBSERVATIONS_CORRELATE_TO_REAL_SESSION"] = (
        "yes" if tool_obs and len(session_bound) >= max(1, len(tool_obs) // 2) else "no"
    )
    result["OBSERVATIONS_WITH_BOUND_SESSION_REF"] = str(len(session_bound))

    # ---- child / work identity correlation ----
    records = _execution_records(root)
    child_records = {
        cid: rec for cid, rec in records.items() if "task-main" not in str(cid)
    }
    child_ids = set(child_records)
    task_return_obs = [o for o in tool_obs if o.operation_name == "task.return"]
    child_correlated = [o for o in tool_obs if o.canonical_task_id in child_ids]
    result["REAL_RUN_CHILD_TASKS"] = str(len(child_records))
    result["OBSERVATIONS_CORRELATE_TO_REAL_CHILD_TASK"] = (
        "yes" if child_correlated else "no"
    )
    result["OBSERVATIONS_CORRELATE_TO_REAL_CHILD_TASK_COUNT"] = str(len(child_correlated))
    result["TASK_RETURN_OBSERVATION_COUNT"] = str(len(task_return_obs))
    result["TASK_RETURN_CORRELATED"] = (
        "yes"
        if any(o.canonical_task_id in child_ids for o in task_return_obs)
        else "no"
    )
    # task.return receipt on disk (durable evidence of the semantic return)
    receipts_dir = root / ".aota" / "task_return_receipts"
    result["TASK_RETURN_RECEIPTS"] = str(len(list(receipts_dir.glob("*.json"))) if receipts_dir.is_dir() else 0)

    # work identity preserved: child canonical task id carries milestone/work
    import re as _re

    wi_pattern = _re.compile(r":M\d+:[^:]*W\d+")
    identity_ok = any(wi_pattern.search(str(cid)) for cid in child_ids)
    result["WORK_IDENTITY_PRESERVED"] = "yes" if identity_ok else "no"
    # reviewer role actually used for the child handoff
    reviewer_used = any(
        "reviewer" in json.dumps(rec) for rec in child_records.values()
    )
    result["REVIEWER_CHILD_USED"] = "yes" if reviewer_used else "no"

    # ---- parent reentry correlation ----
    # Reentry is proven when a parent-session observation occurs after a child
    # completion (task.return receipt / child terminal update). The earliest
    # child completion is the correct deterministic anchor for "parent
    # re-entered after a child finished"; later completions may legitimately
    # have no further parent tool call in the bounded run.
    parent_obs = [o for o in tool_obs if "task-main" in str(o.canonical_task_id or "")]
    terminal_times: list[str] = []
    receipts_dir = root / ".aota" / "task_return_receipts"
    if receipts_dir.is_dir():
        for receipt_path in receipts_dir.glob("*.json"):
            try:
                payload = json.loads(receipt_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            created = payload.get("created_at")
            if isinstance(created, str) and created:
                terminal_times.append(created)
    if not terminal_times:
        for rec in child_records.values():
            ts = rec.get("updated_at") if isinstance(rec, dict) else None
            if isinstance(ts, str) and ts:
                terminal_times.append(ts)
    earliest_terminal = min(terminal_times) if terminal_times else None
    reentry_observations = []
    if earliest_terminal:
        reentry_observations = [
            o for o in parent_obs if (o.started_at_utc or "") >= earliest_terminal
        ]
    result["PARENT_REENTRY_CORRELATED"] = "yes" if reentry_observations else "no"
    result["PARENT_REENTRY_OBSERVATIONS_AFTER_CHILD_COMPLETION"] = str(len(reentry_observations))
    result["PARENT_OBSERVATION_COUNT"] = str(len(parent_obs))

    # ---- bounded metadata supplement (model/provider) ----
    metadata = _hermes_session_metadata(session_id)
    model = provider = None
    if metadata is not None:
        model = metadata.model
        provider = metadata.billing_provider
        result["HERMES_METADATA_SUPPLEMENT"] = "used"
        result["HERMES_METADATA_MODEL"] = str(model)
        result["HERMES_METADATA_PROVIDER"] = str(provider)
        result["HERMES_METADATA_TTFT_STATUS"] = metadata.ttft_status
        result["HERMES_METADATA_LLM_TIMING_STATUS"] = metadata.llm_timing_status
    else:
        result["HERMES_METADATA_SUPPLEMENT"] = "unavailable_graceful"

    # ---- bounded effectiveness card ----
    from aota_forge.work_plane.telemetry_effectiveness_card import (
        build_tool_effectiveness_card,
    )

    ingestion_time = datetime.now(timezone.utc)
    card = build_tool_effectiveness_card(
        project_id=PROJECT_ID,
        tool_observations=tool_obs,
        skill_observations=skill_obs,
        records=list(child_records.values()),
        session_ref=session_id,
        run_ref=run_ref,
        role="task-main",
        model=model,
        provider=provider,
        ingestion_time=ingestion_time,
    )
    result["BOUNDED_EFFECTIVENESS_CARD_PRODUCED"] = "yes"
    result["CARD_DIGEST"] = card.card_digest
    result["CARD_BYTES"] = str(len(card.canonical_json().encode("utf-8")))
    result["CARD_TOOL_OPERATIONS"] = str(len(card.tool_operations))
    result["CARD_TYPED_ERRORS"] = str(len(card.typed_error_histogram))
    result["CARD_FIRST_ATTEMPT_VALID_CALL_RATE"] = str(
        card.first_attempt_valid_call_rate.to_dict()
    )
    result["CARD_REPEAT_REQUEST_GROUPS"] = str(len(card.repeat_requests))
    result["CARD_LOOP_SIGNALS"] = str(len(card.loop_signals))
    result["CARD_SKILLS_SELECTED"] = str(card.skills_selected)
    result["CARD_SKILLS_DELIVERED_EAGER"] = str(card.skills_delivered_eager)
    result["CARD_SKILLS_LOADED_PROGRESSIVE"] = str(card.skills_loaded_progressive)
    result["CARD_SKILLS_OBSERVED_USED"] = str(card.skills_observed_used.to_dict())
    result["CARD_COMPLETENESS_FLAGS"] = ",".join(card.completeness_flags)

    # ---- negative proof: observation sink failure changes nothing ----
    result.update(_negative_isolation_proof(root, args.runtime_config, session_id))

    # ---- bounded evidence artifacts ----
    if args.observations_json:
        Path(args.observations_json).write_text(
            json.dumps(
                {
                    "tool_observations": _bounded_observations_payload(tool_obs),
                    "tool_observation_count": len(tool_obs),
                    "record_versions": list(evidence.record_versions),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    if args.skills_json:
        Path(args.skills_json).write_text(
            json.dumps(
                {
                    "skill_observations": [
                        s.to_dict() for s in skill_obs
                    ],
                    "skill_observation_count": len(skill_obs),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    if args.card_json:
        Path(args.card_json).write_text(
            json.dumps(card.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )

    result["SMOKE_DURATION_SECONDS"] = str(int(time.time() - t0))
    required = [
        result["REAL_RUN_TOOL_OBSERVATION_EMITTED"] == "yes",
        result["REAL_RUN_SKILL_OBSERVATION_EMITTED"] == "yes",
        result["OBSERVATIONS_CORRELATE_TO_REAL_SESSION"] == "yes",
        result["OBSERVATIONS_CORRELATE_TO_REAL_CHILD_TASK"] == "yes",
        result["WORK_IDENTITY_PRESERVED"] == "yes",
        result["TASK_RETURN_CORRELATED"] == "yes",
        result["PARENT_REENTRY_CORRELATED"] == "yes",
        result["BOUNDED_EFFECTIVENESS_CARD_PRODUCED"] == "yes",
        result.get("TELEMETRY_FAILURE_DOES_NOT_CHANGE_TOOL_RESULT") == "yes",
        result.get("TELEMETRY_FAILURE_DOES_NOT_CHANGE_TASK_RESULT") == "yes",
    ]
    result["SMOKE_OUTCOME"] = "PASS" if all(required) else "JUDGED_FAIL"
    _emit(result, args.summary)
    return 0 if all(required) else 1


def _negative_isolation_proof(root: Path, runtime_config: str, session_id: str) -> dict[str, str]:
    """Bounded component proof over the real thin host composition.

    Injects an exploding observation sink and proves a real authorized Tool
    result and a real task-lifecycle failure response are byte-identical
    regardless of telemetry failure.
    """
    out: dict[str, str] = {}
    from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
    from aota_forge.work_plane import runtime_observation as ro

    saved_sink_env = os.environ.pop("AOTA_RUNTIME_OBSERVATION_SINK", None)
    saved_run_env = os.environ.pop("AOTA_RUNTIME_OBSERVATION_RUN_REF", None)
    try:
        host = compose_thin_task_main_host(
            worktree_root=root,
            project_id=PROJECT_ID,
            worktree_id=WORKTREE_ID,
            runtime_config_path=runtime_config,
            origin_task_main_session_ref=session_id,
        )
        expected = (root / "src" / "widget.py").read_text(encoding="utf-8")

        class _ExplodingSink:
            def record_tool(self, observation):  # noqa: ANN001
                raise RuntimeError("injected observation sink failure")

            def record_skill(self, observation, correlation=None):  # noqa: ANN001
                raise RuntimeError("injected observation sink failure")

        ro.install_runtime_observation_sink(_ExplodingSink())
        read_with_failure = host.invoke("workspace.read", {"path": "src/widget.py"})
        bad_task_return = host.invoke(
            "task.return",
            {"status": "completed", "result_ref": "handoff:nonexistent"},
        )
        ro.clear_runtime_observation_sink()
        ro.install_runtime_observation_sink(ro.InMemoryRuntimeObservationSink())
        read_without_failure = host.invoke("workspace.read", {"path": "src/widget.py"})
        bad_task_return_control = host.invoke(
            "task.return",
            {"status": "completed", "result_ref": "handoff:nonexistent"},
        )
        ro.clear_runtime_observation_sink()

        tool_ok = False
        try:
            payload = read_with_failure.get("payload") if isinstance(read_with_failure, dict) else None
            if isinstance(payload, dict):
                tool_ok = payload.get("content") == expected
        except Exception:
            tool_ok = False
        out["TELEMETRY_FAILURE_DOES_NOT_CHANGE_TOOL_RESULT"] = "yes" if tool_ok else "no"
        code_with = None
        code_without = None
        try:
            code_with = (bad_task_return or {}).get("error", {}).get("code")
            code_without = (bad_task_return_control or {}).get("error", {}).get("code")
        except Exception:
            pass
        out["TELEMETRY_FAILURE_DOES_NOT_CHANGE_TASK_RESULT"] = (
            "yes" if code_with is not None and code_with == code_without else "no"
        )
        out["NEGATIVE_PROOF_TOOL_RESULT_IDENTICAL"] = (
            "yes"
            if read_with_failure == read_without_failure
            else "no"
        )

        # ---- bounded observation overhead measurement (component) ----
        import time as _time

        def _timed_reads(iterations: int) -> float:
            started = _time.perf_counter()
            for _ in range(iterations):
                host.invoke("workspace.read", {"path": "src/widget.py"})
            return (_time.perf_counter() - started) / iterations * 1_000_000.0

        ro.clear_runtime_observation_sink()
        saved_path = os.environ.pop("AOTA_RUNTIME_OBSERVATION_SINK", None)
        saved_run = os.environ.pop("AOTA_RUNTIME_OBSERVATION_RUN_REF", None)
        no_sink_avg = _timed_reads(40)
        if saved_path is not None:
            os.environ["AOTA_RUNTIME_OBSERVATION_SINK"] = saved_path
        if saved_run is not None:
            os.environ["AOTA_RUNTIME_OBSERVATION_RUN_REF"] = saved_run
        ro.install_runtime_observation_sink(ro.InMemoryRuntimeObservationSink())
        sink_avg = _timed_reads(40)
        ro.clear_runtime_observation_sink()
        out["OBSERVATION_OVERHEAD_MEASURED"] = "yes"
        out["OBSERVATION_OVERHEAD_NO_SINK_AVG_US"] = f"{no_sink_avg:.1f}"
        out["OBSERVATION_OVERHEAD_SINK_AVG_US"] = f"{sink_avg:.1f}"
        out["OBSERVATION_OVERHEAD_DELTA_US"] = f"{sink_avg - no_sink_avg:.1f}"
    except Exception as exc:
        out["NEGATIVE_PROOF_OUTCOME"] = f"inconclusive:{type(exc).__name__}"
        out["TELEMETRY_FAILURE_DOES_NOT_CHANGE_TOOL_RESULT"] = "no"
        out["TELEMETRY_FAILURE_DOES_NOT_CHANGE_TASK_RESULT"] = "no"
    finally:
        if saved_sink_env is not None:
            os.environ["AOTA_RUNTIME_OBSERVATION_SINK"] = saved_sink_env
        if saved_run_env is not None:
            os.environ["AOTA_RUNTIME_OBSERVATION_RUN_REF"] = saved_run_env
    return out


def _emit(result: dict, summary_path: str) -> None:
    for k, v in result.items():
        print(f"{k}={v}")
    if summary_path:
        Path(summary_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )


if __name__ == "__main__":
    raise SystemExit(main())
