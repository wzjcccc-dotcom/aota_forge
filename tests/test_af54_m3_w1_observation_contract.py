"""AF #54 M3/W1 — Production observation correlation & timing contract tests.

Focused contract proof:
* correlation fields bounded / optional semantics valid
* normalized request digest deterministic, order invariant, privacy-safe
* raw tool input / prompt / output never captured
* timing facts sane; monotonic basis preferred; wall clock not identity
* legacy observation canonical identity preserved when enriched fields absent
* explicit contract version semantics
"""

from __future__ import annotations

import json

import pytest

from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.core.contracts.loader import (
    discover_canonical_project_root,
    load_operation_descriptor_map,
)
from aota_forge.work_plane.observation_correlation import (
    KNOWN_SECRET_FIELDS_EXCLUDED,
    MONOTONIC_DURATION_PREFERRED,
    NORMALIZED_REQUEST_DIGEST_IS_AUTHORITY,
    OBSERVATION_CORRELATION_CONTRACT_VERSION,
    OBSERVATION_CORRELATION_IS_AUTHORITY,
    RAW_PROMPT_CAPTURED,
    RAW_TOOL_INPUT_CAPTURED,
    RAW_TOOL_OUTPUT_CAPTURED,
    SECOND_TELEMETRY_SYSTEM_CREATED,
    WALL_CLOCK_IS_IDENTITY_SOURCE,
    ObservationCorrelationError,
    RuntimeObservationScope,
    ToolObservationContext,
    build_tool_timing,
    clear_runtime_observation_scope,
    compute_normalized_request_digest,
    context_from_binding,
    get_runtime_observation_scope,
    install_runtime_observation_scope,
    utc_now_iso,
)
from aota_forge.work_plane.tool_usage_observation import (
    TOOL_USAGE_OBSERVATION_CONTRACT_VERSION,
    ToolUsageObservation,
    project_tool_usage_observation,
    project_tool_usage_observation_from_invocation,
)


def _descriptor(operation: str):
    return load_operation_descriptor_map(discover_canonical_project_root())[operation]


def _invocation(operation: str, inputs: dict, payload: dict | None = None, *, ok: bool = True):
    request = ToolRequest(operation=_descriptor(operation), inputs=inputs)
    response = ToolResponse.success(payload if payload is not None else {"ok": True}) if ok else ToolResponse.failure(
        {"code": "AUTHORITY_DENIED", "message": "denied"}
    )
    return request, response


class TestCorrelationFlags:
    def test_non_authority_and_privacy_flags(self) -> None:
        assert OBSERVATION_CORRELATION_IS_AUTHORITY is False
        assert NORMALIZED_REQUEST_DIGEST_IS_AUTHORITY is False
        assert WALL_CLOCK_IS_IDENTITY_SOURCE is False
        assert RAW_TOOL_INPUT_CAPTURED is False
        assert RAW_TOOL_OUTPUT_CAPTURED is False
        assert RAW_PROMPT_CAPTURED is False
        assert KNOWN_SECRET_FIELDS_EXCLUDED is True
        assert MONOTONIC_DURATION_PREFERRED is True
        assert SECOND_TELEMETRY_SYSTEM_CREATED is False
        assert TOOL_USAGE_OBSERVATION_CONTRACT_VERSION == OBSERVATION_CORRELATION_CONTRACT_VERSION


class TestNormalizedRequestDigest:
    def test_deterministic_and_order_invariant(self) -> None:
        d1 = compute_normalized_request_digest("workspace.search", {"query": "hello", "limit": 5})
        d2 = compute_normalized_request_digest("workspace.search", {"limit": 5, "query": "hello"})
        assert d1 == d2
        assert d1 is not None and len(d1) == 64

    def test_same_logical_input_same_digest(self) -> None:
        a = compute_normalized_request_digest("workspace.read", {"path": "src/x.py"})
        b = compute_normalized_request_digest("workspace.read", {"path": "src/x.py"})
        assert a == b

    def test_different_input_different_digest(self) -> None:
        a = compute_normalized_request_digest("workspace.read", {"path": "src/x.py"})
        b = compute_normalized_request_digest("workspace.read", {"path": "src/y.py"})
        assert a != b

    def test_operation_is_part_of_identity(self) -> None:
        a = compute_normalized_request_digest("workspace.search", {"query": "q"})
        b = compute_normalized_request_digest("workspace.read", {"query": "q"})
        assert a != b

    def test_secret_fields_removed_not_hashed(self) -> None:
        with_secret = compute_normalized_request_digest(
            "workspace.write", {"path": "a.txt", "mode": "create_only", "api_key": "sk-super-secret"}
        )
        without_secret = compute_normalized_request_digest(
            "workspace.write", {"path": "a.txt", "mode": "create_only", "api_key": "different-secret"}
        )
        no_secret_key = compute_normalized_request_digest(
            "workspace.write", {"path": "a.txt", "mode": "create_only"}
        )
        assert with_secret == without_secret  # removed entirely
        assert with_secret == no_secret_key  # removal, not hashing
        # non-secret fields still participate
        assert with_secret != compute_normalized_request_digest(
            "workspace.write", {"path": "b.txt", "mode": "create_only"}
        )
        # secret raw text must not appear anywhere in the canonical representation
        assert "sk-super-secret" not in json.dumps(with_secret)

    def test_nested_secret_fields_removed(self) -> None:
        a = compute_normalized_request_digest(
            "handoff.write", {"mode": "work_item", "payload": {"authorization": "Bearer abc", "objective": "x"}}
        )
        b = compute_normalized_request_digest(
            "handoff.write", {"mode": "work_item", "payload": {"authorization": "Bearer zzz", "objective": "x"}}
        )
        assert a == b

    def test_long_value_digested_not_retained(self) -> None:
        long_content = "A" * 5000
        d = compute_normalized_request_digest("workspace.write", {"path": "a.txt", "content": long_content})
        assert d is not None
        assert "AAAA" not in d

    def test_long_value_still_distinguishes_content(self) -> None:
        a = compute_normalized_request_digest("workspace.write", {"path": "a.txt", "content": "A" * 5000})
        b = compute_normalized_request_digest("workspace.write", {"path": "a.txt", "content": "B" * 5000})
        assert a != b

    def test_unbounded_or_degenerate_input_fails_closed(self) -> None:
        assert compute_normalized_request_digest("workspace.search", {"query": object()}) is None
        huge = {"k": [{"deep": {"deeper": {"deepest": {"too_deep": 1}}}}]}
        assert compute_normalized_request_digest("workspace.search", huge) is None
        assert compute_normalized_request_digest(123, {}) is None  # type: ignore[arg-type]

    def test_no_raw_input_retained_in_observation(self) -> None:
        digest = compute_normalized_request_digest("workspace.search", {"query": "supersecretquery"})
        assert digest is not None
        assert "supersecretquery" not in digest
        assert RAW_TOOL_INPUT_CAPTURED is False


class TestToolObservationContext:
    def test_optional_semantics(self) -> None:
        # Operations outside a Work task legitimately carry no child identity.
        ctx = ToolObservationContext(project_id="p1", worktree_id="wt1")
        assert ctx.canonical_task_id is None
        assert ctx.milestone_ref is None
        assert ctx.work_item_ref is None
        assert ctx.session_ref is None
        assert ctx.is_authority is False

    def test_fields_bounded(self) -> None:
        with pytest.raises(ObservationCorrelationError):
            ToolObservationContext(session_ref="x" * 513)
        with pytest.raises(ObservationCorrelationError):
            ToolObservationContext(canonical_task_id="x" * 513)
        with pytest.raises(ObservationCorrelationError):
            ToolObservationContext(work_item_ref="")
        with pytest.raises(ObservationCorrelationError):
            ToolObservationContext(milestone_ref="a\x00b")

    def test_work_role_accepts_canonical_role(self) -> None:
        ctx = ToolObservationContext(work_role="task-main")
        assert ctx.work_role is not None and ctx.work_role.value == "task-main"

    def test_binding_extraction_reads_trusted_carriers(self) -> None:
        class _Ref:
            def __init__(self, ref: str) -> None:
                self.ref = ref

        class _Handoff:
            work_role = "reviewer"
            milestone_ref = _Ref("M3")
            work_item_ref = _Ref("W2")

        class _Binding:
            project_id = "proj"
            worktree_id = "wt"
            canonical_task_id = "proj:M3:W2:abcd:12345678"
            handoff = _Handoff()
            session_ref = "20260914_120000_abc123"
            parent_session_ref = None
            run_ref = "run-1"

        ctx = context_from_binding(_Binding())
        assert ctx.work_role is not None and ctx.work_role.value == "reviewer"
        assert ctx.milestone_ref == "M3"
        assert ctx.work_item_ref == "W2"
        assert ctx.canonical_task_id == "proj:M3:W2:abcd:12345678"
        assert ctx.session_ref == "20260914_120000_abc123"
        assert ctx.run_ref == "run-1"

    def test_merge_prefers_explicit_facts(self) -> None:
        base = ToolObservationContext(run_ref="run-1", session_ref="s")
        explicit = ToolObservationContext(session_ref="explicit")
        merged = explicit.merged(base)
        assert merged.session_ref == "explicit"
        assert merged.run_ref == "run-1"


class TestTiming:
    def test_monotonic_duration(self) -> None:
        timing = build_tool_timing(
            started_monotonic_ns=1_000,
            ended_monotonic_ns=3_500,
            started_at_utc="2026-09-14T06:00:00+00:00",
            ended_at_utc="2026-09-14T06:00:01+00:00",
        )
        assert timing.duration_ns == 2_500
        assert timing.timing_basis == "monotonic"

    def test_negative_duration_fails_closed(self) -> None:
        with pytest.raises(ObservationCorrelationError):
            build_tool_timing(started_monotonic_ns=10, ended_monotonic_ns=5)

    def test_wall_only_timing_has_no_duration(self) -> None:
        timing = build_tool_timing(
            started_monotonic_ns=None,
            ended_monotonic_ns=None,
            started_at_utc="2026-09-14T06:00:00+00:00",
        )
        assert timing.duration_ns is None
        assert timing.timing_basis is None

    def test_utc_now_iso_is_timezone_aware(self) -> None:
        from datetime import datetime

        dt = datetime.fromisoformat(utc_now_iso())
        assert dt.tzinfo is not None

    def test_wall_clock_not_identity_source(self) -> None:
        assert WALL_CLOCK_IS_IDENTITY_SOURCE is False


class TestObservationEnrichment:
    def test_legacy_observation_identity_preserved(self) -> None:
        request, response = _invocation("workspace.search", {"query": "q"})
        legacy = project_tool_usage_observation(request, response)
        # enriched projection with no correlation/timing/digest must match
        bare = project_tool_usage_observation_from_invocation(
            operation_name=request.operation.name,
            contract_hash=request.operation.contract_hash(),
            inputs=request.inputs,
            response=response,
        )
        assert legacy.canonical_json() == bare.canonical_json()
        assert legacy.observation_contract_version is None

    def test_enriched_fields_present_and_versioned(self) -> None:
        request, response = _invocation("workspace.search", {"query": "q"})
        digest = compute_normalized_request_digest("workspace.search", request.inputs)
        ctx = ToolObservationContext(
            project_id="proj",
            worktree_id="wt",
            work_role="coder",
            session_ref="sess-1",
            canonical_task_id="proj:M3:W1:abcd:12345678",
            milestone_ref="M3",
            work_item_ref="W1",
            run_ref="run-1",
        )
        timing = build_tool_timing(
            started_monotonic_ns=100,
            ended_monotonic_ns=400,
            started_at_utc="2026-09-14T06:00:00+00:00",
            ended_at_utc="2026-09-14T06:00:01+00:00",
        )
        obs = project_tool_usage_observation(
            request, response, correlation=ctx, timing=timing, normalized_request_digest=digest
        )
        assert obs.session_ref == "sess-1"
        assert obs.canonical_task_id == "proj:M3:W1:abcd:12345678"
        assert obs.milestone_ref == "M3"
        assert obs.work_item_ref == "W1"
        assert obs.normalized_request_digest == digest
        assert obs.duration_ns == 300
        assert obs.timing_basis == "monotonic"
        assert obs.observation_contract_version == OBSERVATION_CORRELATION_CONTRACT_VERSION
        # roundtrip preserves identity
        again = ToolUsageObservation.from_dict(obs.to_dict())
        assert again.compute_digest() == obs.compute_digest()

    def test_no_raw_payload_in_canonical_observation(self) -> None:
        request, response = _invocation(
            "workspace.write",
            {"path": "a.txt", "content": "RAW-SECRET-CONTENT", "mode": "create_only"},
            {"bytes_written": 17},
        )
        digest = compute_normalized_request_digest("workspace.write", request.inputs)
        obs = project_tool_usage_observation(request, response, normalized_request_digest=digest)
        canonical = obs.canonical_json()
        assert "RAW-SECRET-CONTENT" not in canonical
        assert "a.txt" not in canonical  # raw path input not retained either
        assert obs.result_digest is not None  # digest of result only

    def test_timing_validation_fail_closed(self) -> None:
        from aota_forge.work_plane.observation_correlation import ToolObservationTiming

        with pytest.raises(ObservationCorrelationError):
            ToolObservationTiming(duration_ns=-1)
        with pytest.raises(ObservationCorrelationError):
            ToolObservationTiming(timing_basis="monotonic")  # requires duration
        with pytest.raises(ObservationCorrelationError):
            ToolObservationTiming(started_at_utc="not-a-date")


class TestRuntimeObservationScope:
    def teardown_method(self) -> None:
        clear_runtime_observation_scope()

    def test_scope_install_and_clear(self) -> None:
        install_runtime_observation_scope(RuntimeObservationScope(run_ref="run-9", session_ref="sess-9"))
        scope = get_runtime_observation_scope()
        assert scope.run_ref == "run-9"
        assert scope.session_ref == "sess-9"
        clear_runtime_observation_scope()
        assert get_runtime_observation_scope().run_ref is None
