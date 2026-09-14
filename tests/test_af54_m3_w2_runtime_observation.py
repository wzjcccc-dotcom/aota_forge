"""AF #54 M3/W2 — Thin runtime Tool / Skill observation wiring tests.

Focused production-wiring proof:
* exactly one bounded Tool observation per governed invocation (success,
  typed failure, authority denial, invalid input, unknown operation)
* observation happens only after the real result is known
* observation sink failure never changes the real Tool result
* role.bootstrap emits real Skill selection/delivery observations
* skill.open emits the real progressive-load observation
* bounded per-run evidence file roundtrip; overflow marks truncation
* operator env opt-in sink
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aota_forge.composition.worker_vertical_slice import build_worker_binding
from aota_forge.core_ingress import CanonicalDispatchBinding, dispatch_tool_operation
from aota_forge.mcp_transport import _to_canonical_binding
from aota_forge.work_plane.handoff import SemanticReference, TaskHandoff
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane import runtime_observation as ro


def _handoff(role: AgentWorkRole = AgentWorkRole.CODER) -> TaskHandoff:
    return TaskHandoff(
        work_role=role,
        task_kind="af54-m3-w2-test",
        objective="bounded observation wiring objective",
        bounded_scope="work/bounded-only",
        validation_expectations=("output matches",),
        semantic_stop_expectations=("stop on denial",),
        work_item_ref=SemanticReference(ref="W2"),
        milestone_ref=SemanticReference(ref="M3"),
    )


def _binding(root: Path, *, role: AgentWorkRole = AgentWorkRole.CODER) -> CanonicalDispatchBinding:
    worker = build_worker_binding(
        root=root,
        project_id="aota_forge",
        worktree_id="wt-m3w2",
        canonical_task_id="aota_forge:M3:W2:abcd:12345678",
        handoff=_handoff(role),
    )
    return _to_canonical_binding(worker)


@pytest.fixture(autouse=True)
def _clean_sink():
    ro.clear_runtime_observation_sink()
    ro.reset_runtime_observation_health()
    yield
    ro.clear_runtime_observation_sink()
    ro.reset_runtime_observation_health()


class TestToolObservationWiring:
    def test_one_invocation_one_observation_success(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        assert response.ok
        observations = sink.tool_observations()
        assert len(observations) == 1
        obs = observations[0]
        assert obs.is_success is True
        assert obs.operation_name == "workspace.read"
        # result known before observation
        assert obs.result_digest is not None
        assert obs.result_ref is not None
        # production correlation/timing/request identity present
        assert obs.normalized_request_digest is not None
        assert obs.duration_ns is not None and obs.duration_ns >= 0
        assert obs.timing_basis == "monotonic"
        assert obs.started_at_utc is not None
        assert obs.canonical_task_id == "aota_forge:M3:W2:abcd:12345678"
        assert obs.work_role is not None and obs.work_role.value == "coder"
        assert obs.milestone_ref == "M3"
        assert obs.work_item_ref == "W2"
        assert obs.project_id == "aota_forge"
        # exactly one per invocation across repeats
        dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        assert len(sink.tool_observations()) == 2
        assert ro.runtime_observation_health()["tool_emitted"] == 2

    def test_typed_failure_observation_truthful(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("workspace.read", {"path": "missing.txt"}, binding)
        assert response.ok is False
        obs = sink.tool_observations()[0]
        assert obs.is_success is False
        assert obs.error_code == "NOT_FOUND"
        assert obs.outcome_class in ("failure_invalid_input", "failure_provider")
        assert obs.normalized_request_digest is not None

    def test_authority_denied_observation(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        # reviewer has no workspace.write mutation authority → denial observed
        binding = _binding(tmp_path, role=AgentWorkRole.REVIEWER)
        response = dispatch_tool_operation(
            "workspace.write", {"path": "x.txt", "content": "hi", "mode": "create_only"}, binding
        )
        assert response.ok is False
        obs = sink.tool_observations()[0]
        assert obs.is_success is False
        assert obs.error_code == "AUTHORITY_DENIED"
        assert obs.outcome_class == "failure_authority_denied"

    def test_invalid_input_observation(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("workspace.search", {"query": 123}, binding)  # type: ignore[dict-item]
        assert response.ok is False
        obs = sink.tool_observations()[0]
        assert obs.is_success is False
        assert obs.outcome_class == "failure_invalid_input"

    def test_unknown_operation_observation(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("definitely.unknown_op", {}, binding)
        assert response.ok is False
        obs = sink.tool_observations()[0]
        assert obs.is_success is False
        assert obs.error_code == "UNKNOWN_OPERATION"

    def test_sink_failure_never_changes_tool_result(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("payload", encoding="utf-8")

        class _ExplodingSink:
            def record_tool(self, observation):  # noqa: ANN001
                raise RuntimeError("sink exploded")

            def record_skill(self, observation, correlation=None):  # noqa: ANN001
                raise RuntimeError("sink exploded")

        ro.install_runtime_observation_sink(_ExplodingSink())
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        assert response.ok is True
        assert response.payload is not None
        assert response.payload.get("content") == "payload"
        health = ro.runtime_observation_health()
        assert health["failure_count"] >= 1
        assert "record_tool" in str(health["last_failure"])

    def test_correlation_from_binding_session_ref(self, tmp_path: Path) -> None:
        import dataclasses

        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = dataclasses.replace(
            _binding(tmp_path), session_ref="20260914_120000_abc123", run_ref="run-42"
        )
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        obs = sink.tool_observations()[0]
        assert obs.session_ref == "20260914_120000_abc123"
        # run_ref participates through the process scope only; explicit binding
        # session facts must win over process defaults

    def test_observation_does_not_capture_raw_input(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        dispatch_tool_operation("workspace.search", {"query": "RAW-QUERY-STRING"}, binding)
        obs = sink.tool_observations()[0]
        assert "RAW-QUERY-STRING" not in obs.canonical_json()


class TestSkillObservationWiring:
    def test_role_bootstrap_skill_observations_via_dispatch(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("role.bootstrap", {}, binding)
        assert response.ok, response.error
        observations = sink.skill_observations()
        assert len(observations) >= 2
        eager = [o.observation for o in observations if o.observation.delivery == "eager"]
        progressive = [o.observation for o in observations if o.observation.delivery == "progressive"]
        assert eager, "expected eager Skill delivery observations"
        assert progressive, "expected progressive Skill availability observations"
        for observed in observations:
            assert observed.observation.namespace == "coder"
            assert observed.correlation.get("canonical_task_id") == "aota_forge:M3:W2:abcd:12345678"
            assert observed.correlation.get("work_role") == "coder"
        # selection/delivery distinguished; no observed-use claim
        assert ro.SKILL_DELIVERED_IS_OBSERVED_USED is False

    def test_skill_open_progressive_load_observation(self, tmp_path: Path) -> None:
        sink = ro.InMemoryRuntimeObservationSink()
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        dispatch_tool_operation("role.bootstrap", {}, binding)
        progressive_ref = next(
            o.observation.ref
            for o in sink.skill_observations()
            if o.observation.delivery == "progressive" and o.observation.ref
        )
        response = dispatch_tool_operation("skill.open", {"ref": progressive_ref}, binding)
        assert response.ok, response.error
        loaded = [o.observation for o in sink.skill_observations() if o.observation.event_type == "execution_materialized"]
        assert len(loaded) == 1
        assert loaded[0].delivery == "progressive"
        assert loaded[0].ref == progressive_ref

    def test_skill_sink_failure_does_not_change_bootstrap(self, tmp_path: Path) -> None:
        class _ExplodingSink:
            def record_tool(self, observation):  # noqa: ANN001
                raise RuntimeError("boom")

            def record_skill(self, observation, correlation=None):  # noqa: ANN001
                raise RuntimeError("boom")

        ro.install_runtime_observation_sink(_ExplodingSink())
        binding = _binding(tmp_path)
        response = dispatch_tool_operation("role.bootstrap", {}, binding)
        assert response.ok, response.error
        assert response.payload is not None
        assert "BASE_SKILLS" in response.payload
        assert ro.runtime_observation_health()["failure_count"] >= 1


class TestEvidenceSink:
    def test_file_sink_roundtrip_bounded_and_redacted(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.jsonl"
        sink = ro.FileRuntimeObservationSink(path, max_records=16)
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        (tmp_path / "a.txt").write_text("content", encoding="utf-8")
        dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        dispatch_tool_operation("role.bootstrap", {}, binding)
        evidence = ro.load_runtime_observation_evidence(path)
        # both dispatched operations are governed Tool invocations, and the
        # Skill observations are emitted at the same Skill seam
        assert len(evidence.tool_observations) == 2
        assert {o.operation_name for o in evidence.tool_observations} == {"workspace.read", "role.bootstrap"}
        assert len(evidence.skill_observations) >= 2
        assert evidence.truncated is False
        assert evidence.invalid_line_count == 0
        assert ro.OBSERVATION_EVIDENCE_RECORD_VERSION in evidence.record_versions
        # no raw inputs anywhere in the file
        raw = path.read_text(encoding="utf-8")
        assert "a.txt" not in raw
        assert '"query"' not in raw

    def test_file_sink_overflow_marks_truncation(self, tmp_path: Path) -> None:
        path = tmp_path / "evidence.jsonl"
        sink = ro.FileRuntimeObservationSink(path, max_records=1)
        ro.install_runtime_observation_sink(sink)
        binding = _binding(tmp_path)
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        assert sink.truncated is True
        evidence = ro.load_runtime_observation_evidence(path)
        assert evidence.truncated is True
        assert len(evidence.tool_observations) == 1

    def test_env_configured_sink(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "env-evidence.jsonl"
        monkeypatch.setenv(ro.OBSERVATION_SINK_ENV, str(path))
        monkeypatch.setenv(ro.OBSERVATION_RUN_REF_ENV, "run-env-1")
        monkeypatch.setenv(ro.OBSERVATION_SESSION_REF_ENV, "sess-env-1")
        binding = _binding(tmp_path)
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        assert path.is_file()
        evidence = ro.load_runtime_observation_evidence(path)
        assert len(evidence.tool_observations) == 1
        obs = evidence.tool_observations[0]
        # process-scope fallback provides run/session correlation facts
        assert obs.session_ref == "sess-env-1"
        ro.clear_runtime_observation_sink()

    def test_no_sink_installed_is_valid_noop(self, tmp_path: Path) -> None:
        binding = _binding(tmp_path)
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        response = dispatch_tool_operation("workspace.read", {"path": "a.txt"}, binding)
        assert response.ok
