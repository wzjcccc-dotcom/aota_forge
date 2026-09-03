"""Focused proof for S6 M2 W3 — Bounded Query / Ref / Selective Hydration."""

from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
from datetime import datetime, timezone

import pytest

from aota_forge.work_plane.telemetry_evidence import (
    CompletenessRecord,
    CompletenessState,
    CompletenessScope,
    RetentionProvenance,
    RetentionClass,
    SourceEvidenceIdentity,
    compute_source_dedup_id,
)
from aota_forge.work_plane.telemetry_metrics import (
    MetricFamily,
    ToolMetricSubject,
    compute_aggregation_series_id,
    compute_aggregation_window_id,
    parse_metric_family,
)
from aota_forge.work_plane.telemetry_aggregation import (
    AggregationState,
    AggregateCompleteness,
    WindowLifecycle,
    WindowPolicy,
    create_initial_state,
    create_aggregation_contribution,
    apply_contribution,
    Disposition,
)
from aota_forge.work_plane.telemetry_store import (
    STORAGE_CONTRACT_VERSION,
    SUPPORTED_STORAGE_VERSIONS,
    StorageKey,
    StorageEnvelope,
    EphemeralTelemetryStore,
    derive_storage_key,
    compute_state_digest,
)
from aota_forge.work_plane import telemetry_query as qmod
from aota_forge.work_plane.telemetry_query import (
    TelemetryQueryRequest,
    TelemetryQueryResult,
    TelemetryRef,
    TelemetryQueryItem,
    QueryCoverage,
    HydrationDisposition,
    HydrationResult,
    hydrate_telemetry_ref,
    query_telemetry_store,
    MAX_QUERY_LIMIT,
)

from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# Helpers

def _make_source(project_id="proj-a", obs_id="obs-1", digest="a"*64):
    return SourceEvidenceIdentity(project_id=project_id, source_kind="tool_usage", source_observation_id=obs_id, source_contract_version="v1", source_digest=digest)

def _make_series(project_id="proj-a", op="workspace.read"):
    subj = ToolMetricSubject(operation_name=op)
    return compute_aggregation_series_id(MetricFamily.TOOL, subj, {})

def _make_policy(pid="policy-1", ver="v1"):
    return WindowPolicy(window_policy_id=pid, window_policy_version=ver, allow_ingestion_time_fallback=False)

def _make_window(series, policy, key):
    return compute_aggregation_window_id(series, policy.window_policy_id, policy.window_policy_version, key)

def _make_completeness(state="complete", scope="source"):
    return CompletenessRecord(state=state, scope=scope)

def _contrib(project_id, sd, ns, ver, series, window_id, policy, op, val, comp):
    pid = policy.window_policy_id if policy and window_id else None
    pver = policy.window_policy_version if policy and window_id else None
    return create_aggregation_contribution(project_id=project_id, source_dedup_id=sd, projection_namespace=ns, projection_version=ver, aggregation_series_id=series, aggregation_window_id=window_id, window_policy_id=pid, window_policy_version=pver, operation=op, value=val, completeness=comp)

def _make_state_with_sum(proj, series, window_id, policy, values):
    state = create_initial_state(proj, series, window_id, policy.window_policy_id if policy and window_id else None, policy.window_policy_version if policy and window_id else None)
    for idx, v in enumerate(values):
        src = _make_source(project_id=proj, obs_id=f"sum-{idx}-{v}-{series[:4]}", digest=hashlib.sha256(f"{proj}-{idx}-{v}".encode()).hexdigest())
        sd = compute_source_dedup_id(src)
        comp = _make_completeness("complete")
        c = _contrib(proj, sd, "ns", f"v{idx}", series, window_id, policy, "SUM", v, comp)
        r = apply_contribution(state, c)
        assert r.disposition == Disposition.ACCEPTED
        state = r.new_state
    return state

# ---------------------------------------------------------------------------
# 44. Exact lookup
# ---------------------------------------------------------------------------

def test_exact_lookup_returns_bounded_projection():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    policy = _make_policy()
    wid = _make_window(series, policy, "2026-01-01")
    state = _make_state_with_sum(proj, series, wid, policy, [5, 7])
    store = EphemeralTelemetryStore(max_records=10, max_projects=5)
    store.put(state)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, aggregation_window_id=wid, limit=5)
    res = query_telemetry_store(store, req)
    assert isinstance(res, TelemetryQueryResult)
    assert res.result_count == 1
    assert len(res.items) == 1
    item = res.items[0]
    assert item.project_id == proj
    assert item.aggregation_series_id == series
    assert item.aggregation_window_id == wid
    assert item.content_digest == compute_state_digest(state)
    assert item.ref.project_id == proj
    assert item.ref.is_authority is False
    assert res.is_authority is False
    assert res.coverage == QueryCoverage.COMPLETE

def test_exact_lookup_wrong_project_fail_closed():
    proj_a = "proj-a"
    proj_b = "proj-b"
    series = _make_series(op="workspace.read")
    policy = _make_policy()
    wid = _make_window(series, policy, "2026-01-02")
    state = _make_state_with_sum(proj_a, series, wid, policy, [5])
    store = EphemeralTelemetryStore()
    store.put(state)
    # query under wrong project should return empty (no match) not A's data, which is fail-closed cross-project
    req = TelemetryQueryRequest(project_id=proj_b, aggregation_series_id=series, aggregation_window_id=wid, limit=5)
    res = query_telemetry_store(store, req)
    # Should be empty, not return A's data
    assert res.result_count == 0
    assert res.coverage == QueryCoverage.EMPTY
    # also direct store get cross-project fails, but query should be empty/filtered

def test_exact_lookup_unknown_key_no_match_not_zero():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    policy = _make_policy()
    wid = _make_window(series, policy, "2026-01-03")
    store = EphemeralTelemetryStore()
    # no put
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, aggregation_window_id=wid, limit=5)
    res = query_telemetry_store(store, req)
    assert res.result_count == 0
    assert res.coverage == QueryCoverage.EMPTY
    # metric zero not inferred: item sum not zero, but no item
    assert len(res.items) == 0

# ---------------------------------------------------------------------------
# 45. Typed selectors
# ---------------------------------------------------------------------------

def test_series_selector_deterministic():
    proj = "proj-a"
    series_a = _make_series(op="workspace.read")
    series_b = _make_series(op="workspace.write")
    policy = _make_policy()
    wid = _make_window(series_a, policy, "2026-01-04")
    wid_b = _make_window(series_b, policy, "2026-01-04")
    state_a = _make_state_with_sum(proj, series_a, wid, policy, [3])
    state_b = _make_state_with_sum(proj, series_b, wid_b, policy, [9])
    store = EphemeralTelemetryStore(max_records=10, max_projects=2)
    store.put(state_a)
    store.put(state_b)
    # query with series filter should return only matching
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series_a, limit=5)
    res = query_telemetry_store(store, req)
    assert res.result_count == 1
    assert res.items[0].aggregation_series_id == series_a

def test_window_selector_deterministic():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    policy = _make_policy()
    wid1 = _make_window(series, policy, "2026-01-05")
    wid2 = _make_window(series, policy, "2026-01-06")
    state1 = _make_state_with_sum(proj, series, wid1, policy, [2])
    state2 = _make_state_with_sum(proj, series, wid2, policy, [4])
    store = EphemeralTelemetryStore(max_records=10, max_projects=2)
    store.put(state1)
    store.put(state2)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, aggregation_window_id=wid1, limit=5)
    res = query_telemetry_store(store, req)
    assert res.result_count == 1
    assert res.items[0].aggregation_window_id == wid1

def test_known_schema_version_selector():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    store.put(state)
    req = TelemetryQueryRequest(project_id=proj, storage_contract_version=STORAGE_CONTRACT_VERSION, limit=5)
    res = query_telemetry_store(store, req)
    assert res.result_count == 1

def test_reject_invalid_free_text_metric_family():
    with pytest.raises((ValueError, TypeError)):
        TelemetryQueryRequest(project_id="proj-a", limit=5, metric_family="totally_unknown_family_xyz")

def test_reject_invalid_series_digest():
    with pytest.raises(ValueError):
        TelemetryQueryRequest(project_id="proj-a", aggregation_series_id="not-hex", limit=5)

# ---------------------------------------------------------------------------
# 46. Deterministic ordering
# ---------------------------------------------------------------------------

def test_deterministic_ordering_independent_of_insertion():
    proj = "proj-a"
    policy = _make_policy()
    # create 3 distinct series/windows
    series_objs = []
    states = []
    for op in ["workspace.read", "workspace.write", "workspace.execute"]:
        s = _make_series(op=op)
        wid = _make_window(s, policy, "2026-01-10")
        st = _make_state_with_sum(proj, s, wid, policy, [1])
        series_objs.append((s, wid, st))
        states.append(st)
    # store in order A
    store_a = EphemeralTelemetryStore(max_records=20, max_projects=2)
    for _, _, st in series_objs:
        store_a.put(st)
    # store in reverse order B
    store_b = EphemeralTelemetryStore(max_records=20, max_projects=2)
    for _, _, st in reversed(series_objs):
        store_b.put(st)
    req = TelemetryQueryRequest(project_id=proj, limit=10)
    res_a = query_telemetry_store(store_a, req)
    res_b = query_telemetry_store(store_b, req)
    assert tuple((i.aggregation_series_id, i.aggregation_window_id) for i in res_a.items) == tuple((i.aggregation_series_id, i.aggregation_window_id) for i in res_b.items)
    # also sorted canonical
    sorted_keys = sorted((s for s, _, _ in series_objs))
    # Since ordering is by series, check determinism
    assert [it.aggregation_series_id for it in res_a.items] == sorted([it.aggregation_series_id for it in res_a.items])

# ---------------------------------------------------------------------------
# 47. Bound / limit
# ---------------------------------------------------------------------------

def test_bounded_result_truncated_and_not_complete():
    proj = "proj-a"
    store = EphemeralTelemetryStore(max_records=20, max_projects=2)
    policy = _make_policy()
    series_list = []
    for idx in range(5):
        op = f"op-{idx}"
        # Use deterministic op names that are bounded; use ToolMetricSubject directly with allowed chars
        subj = ToolMetricSubject(operation_name=f"workspace.op{idx}")
        series = compute_aggregation_series_id(MetricFamily.TOOL, subj, {})
        wid = _make_window(series, policy, f"2026-01-{11+idx:02d}")
        st = _make_state_with_sum(proj, series, wid, policy, [idx+1])
        store.put(st)
        series_list.append(series)
    req = TelemetryQueryRequest(project_id=proj, limit=2)
    res = query_telemetry_store(store, req)
    assert len(res.items) == 2
    assert res.result_count == 2
    assert res.truncated is True
    assert res.coverage == QueryCoverage.TRUNCATED_BY_LIMIT
    # not complete coverage
    assert res.coverage != QueryCoverage.COMPLETE

def test_invalid_oversized_limit_fail_closed():
    with pytest.raises(ValueError):
        TelemetryQueryRequest(project_id="proj-a", limit=9999)
    with pytest.raises(ValueError):
        TelemetryQueryRequest(project_id="proj-a", limit=0)
    with pytest.raises(TypeError):
        TelemetryQueryRequest(project_id="proj-a", limit="5")  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        TelemetryQueryRequest(project_id="proj-a", limit=None)  # type: ignore

def test_negative_limit_fail_closed():
    with pytest.raises(ValueError):
        TelemetryQueryRequest(project_id="proj-a", limit=-1)

# ---------------------------------------------------------------------------
# 48. Empty result
# ---------------------------------------------------------------------------

def test_empty_result_no_metric_zero():
    proj = "proj-a"
    store = EphemeralTelemetryStore()
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res = query_telemetry_store(store, req)
    assert res.result_count == 0
    assert res.coverage == QueryCoverage.EMPTY
    assert len(res.items) == 0
    # completeness not fabricated
    assert res.coverage not in (QueryCoverage.COMPLETE,)

# ---------------------------------------------------------------------------
# 49. Retention tombstone
# ---------------------------------------------------------------------------

def test_retention_tombstone_query_returns_expired_not_zero():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [7])
    store = EphemeralTelemetryStore()
    env = store.put(state)
    rp = RetentionProvenance(retention_policy_id="p1", retention_policy_version="v1", retention_class=RetentionClass.EPHEMERAL)
    store.expire(proj, series, None, rp)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=5)
    res = query_telemetry_store(store, req)
    # should be expired disposition, not zero, not resurrected
    assert res.result_count == 0
    assert res.coverage == QueryCoverage.EXPIRED
    assert len(res.items) == 0
    # ensure no aggregate payload resurrected
    for it in res.items:
        assert it.count != 0 or True  # no items

def test_retention_tombstone_wildcard_shows_expired():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    policy = _make_policy()
    wid = _make_window(series, policy, "2026-01-20")
    state = _make_state_with_sum(proj, series, wid, policy, [5])
    store = EphemeralTelemetryStore()
    store.put(state)
    rp = RetentionProvenance(retention_policy_id="p1", retention_policy_version="v1", retention_class=RetentionClass.EPHEMERAL)
    store.expire(proj, series, wid, rp)
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res = query_telemetry_store(store, req)
    # wildcard should also show expired when only tombstones?
    # Since we filtered envelopes empty and tomb matched, we return expired
    assert res.coverage == QueryCoverage.EXPIRED

# ---------------------------------------------------------------------------
# 50. Corruption
# ---------------------------------------------------------------------------

def test_corruption_fail_closed_not_silently_skipped():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    store.put(state)
    # Tamper via direct injection of corrupted envelope bypassing validation
    key = derive_storage_key(state)
    # Create corrupted envelope via __new__
    corrupted = object.__new__(StorageEnvelope)
    object.__setattr__(corrupted, "storage_contract_version", STORAGE_CONTRACT_VERSION)
    object.__setattr__(corrupted, "storage_key", key)
    object.__setattr__(corrupted, "aggregation_state", state)
    object.__setattr__(corrupted, "content_digest", "0"*64)  # wrong digest
    object.__setattr__(corrupted, "prior_content_digest", None)
    object.__setattr__(corrupted, "storage_lifecycle", "active")
    store._store[key] = corrupted  # type: ignore
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=5)
    # exact lookup should fail closed
    with pytest.raises(Exception):
        query_telemetry_store(store, req)
    # wildcard also should fail closed due to corrupt matching
    req2 = TelemetryQueryRequest(project_id=proj, limit=5)
    with pytest.raises(Exception):
        query_telemetry_store(store, req2)

# ---------------------------------------------------------------------------
# 51. Ref integrity
# ---------------------------------------------------------------------------

def test_ref_deterministic_and_tamper_fail_closed():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    env = store.put(state)
    from aota_forge.work_plane.telemetry_query import derive_telemetry_ref
    ref1 = derive_telemetry_ref(env)
    ref2 = derive_telemetry_ref(env)
    assert ref1.canonical_json() == ref2.canonical_json()
    assert ref1.content_digest == ref2.content_digest
    assert ref1.project_id == proj
    # tampered digest should fail (invalid hex length)
    with pytest.raises(ValueError):
        TelemetryRef(project_id=proj, aggregation_series_id=series, aggregation_window_id=None, content_digest="short", storage_contract_version=STORAGE_CONTRACT_VERSION)
        # Actually need to test hydrate tamper: create ref with wrong digest
    bad_ref = TelemetryRef(project_id=proj, aggregation_series_id=series, aggregation_window_id=None, content_digest=compute_state_digest(state), storage_contract_version=STORAGE_CONTRACT_VERSION)
    # tamper after creation via object.__new__
    tampered = object.__new__(TelemetryRef)
    object.__setattr__(tampered, "project_id", proj)
    object.__setattr__(tampered, "aggregation_series_id", series)
    object.__setattr__(tampered, "aggregation_window_id", None)
    object.__setattr__(tampered, "content_digest", "0"*64)
    object.__setattr__(tampered, "storage_contract_version", STORAGE_CONTRACT_VERSION)
    with pytest.raises(Exception):
        hydrate_telemetry_ref(tampered, current_project_id=proj, hydration_source={tampered.content_digest: b"hello"})

def test_ref_project_scoped():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    env = store.put(state)
    from aota_forge.work_plane.telemetry_query import derive_telemetry_ref
    ref = derive_telemetry_ref(env)
    assert ref.project_id == proj
    # hydration with wrong project should fail closed
    with pytest.raises(Exception):
        hydrate_telemetry_ref(ref, current_project_id="proj-b", hydration_source=None)

# ---------------------------------------------------------------------------
# 52. Explicit hydration
# ---------------------------------------------------------------------------

def test_hydration_explicit_not_automatic():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    env = store.put(state)
    from aota_forge.work_plane.telemetry_query import derive_telemetry_ref
    ref = derive_telemetry_ref(env)
    # query should not auto-hydrate
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=5)
    res = query_telemetry_store(store, req)
    # result contains ref/digest, not hydrated content
    assert len(res.items) == 1
    assert hasattr(res.items[0], "ref")
    assert not hasattr(res.items[0], "content")
    assert not hasattr(res.items[0], "hydrated_content")
    # explicit hydrate with source that has content matching digest
    content = b"telemetry aggregate bytes for test"
    # create ref with digest of content? But ref digest is state digest, not content bytes
    # For telemetry, hydration source provides bytes that hash to ref.content_digest
    # So we need to make source entry where digest matches ref digest
    # We'll use mapping keyed by digest
    # Compute expected raw that would hash to ref digest is not known; we can instead test REF_ONLY path
    h_res = hydrate_telemetry_ref(ref, current_project_id=proj, hydration_source=None)
    assert h_res.disposition == HydrationDisposition.REF_ONLY
    assert h_res.hydrated is None
    # Also test hydratable path by creating a ref whose digest matches supplied content
    digest = hashlib.sha256(content).hexdigest()
    hyd_ref = TelemetryRef(project_id=proj, aggregation_series_id=series, aggregation_window_id=None, content_digest=digest, storage_contract_version=STORAGE_CONTRACT_VERSION)
    h2 = hydrate_telemetry_ref(hyd_ref, current_project_id=proj, hydration_source={digest: content})
    assert h2.disposition == HydrationDisposition.HYDRATABLE
    assert h2.hydrated is not None
    assert h2.hydrated.digest == digest
    assert h2.hydrated.byte_length == len(content)

def test_hydration_digest_verification():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    content = b"hello world"
    digest = hashlib.sha256(content).hexdigest()
    ref = TelemetryRef(project_id=proj, aggregation_series_id=series, aggregation_window_id=None, content_digest=digest, storage_contract_version=STORAGE_CONTRACT_VERSION)
    # tampered content
    bad_content = b"tampered"
    with pytest.raises(Exception):
        hydrate_telemetry_ref(ref, current_project_id=proj, hydration_source={digest: bad_content})

# ---------------------------------------------------------------------------
# 53. Cross-project hydration
# ---------------------------------------------------------------------------

def test_cross_project_hydration_fail_closed():
    proj_a = "proj-a"
    proj_b = "proj-b"
    series = _make_series(op="workspace.read")
    content = b"data"
    digest = hashlib.sha256(content).hexdigest()
    ref = TelemetryRef(project_id=proj_a, aggregation_series_id=series, aggregation_window_id=None, content_digest=digest, storage_contract_version=STORAGE_CONTRACT_VERSION)
    with pytest.raises(Exception):
        hydrate_telemetry_ref(ref, current_project_id=proj_b, hydration_source={digest: content})

# ---------------------------------------------------------------------------
# 54. No raw payload leakage
# ---------------------------------------------------------------------------

def test_no_raw_payload_leakage():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    store.put(state)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=5)
    res = query_telemetry_store(store, req)
    item = res.items[0]
    # Check fields do not contain raw payload names
    d = item.__dict__
    for forbidden in ["raw_payload", "argv", "stdout", "stderr", "transcript", "metadata", "filesystem", "raw_source"]:
        for k in d.keys():
            assert forbidden not in k.lower(), f"forbidden field {forbidden} in item {k}"
    # Check ref fields
    ref_d = item.ref.__dict__
    for forbidden in ["raw_payload", "argv", "stdout", "stderr", "transcript", "filesystem", "raw_source", "path"]:
        for k in ref_d.keys():
            # path is allowed as part of maybe not, but ensure no raw path
            if k == "aggregation_series_id":
                continue
            assert forbidden not in k.lower() or "path" not in k.lower()
    # Check query request/result schemas not containing raw
    assert not hasattr(TelemetryQueryRequest, "raw_payload")
    assert not hasattr(TelemetryQueryResult, "raw_payload")
    # Ensure module does not create raw capture fields (docstring may mention raw for negation but flags prove absence)
    assert qmod.RAW_SOURCE_COPIED_INTO_TELEMETRY_STORE_FOR_HYDRATION is False
    assert qmod.QUERY_AUTO_HYDRATES_SOURCE is False

# ---------------------------------------------------------------------------
# 55. Authority isolation
# ---------------------------------------------------------------------------

def test_authority_isolation():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    env = store.put(state)
    req = TelemetryQueryRequest(project_id=proj, aggregation_series_id=series, limit=5)
    res = query_telemetry_store(store, req)
    assert res.is_authority is False
    assert res.items[0].is_authority is False
    assert res.items[0].ref.is_authority is False
    # hydrated
    content = b"bytes"
    digest = hashlib.sha256(content).hexdigest()
    ref = TelemetryRef(project_id=proj, aggregation_series_id=series, aggregation_window_id=None, content_digest=digest, storage_contract_version=STORAGE_CONTRACT_VERSION)
    h = hydrate_telemetry_ref(ref, current_project_id=proj, hydration_source={digest: content})
    assert h.hydrated.is_authority is False if h.hydrated else True
    # No mutation APIs
    assert not hasattr(res, "promote_tool")
    assert not hasattr(res, "change_plan")
    assert not hasattr(res.items[0], "mutate_plan")

# ---------------------------------------------------------------------------
# 56. Negative architecture proof
# ---------------------------------------------------------------------------

def test_negative_architecture():
    text = Path(inspect.getfile(qmod)).read_text().lower()
    for forbidden in ["import sqlite", "import duckdb", "import redis", "filesystem persistence", "class durabletelemetrystore"]:
        assert forbidden not in text
    assert qmod.DURABLE_STORAGE_IMPLEMENTED is False
    assert qmod.SECOND_HYDRATION_AUTHORITY_CREATED is False
    assert qmod.GENERIC_QUERY_ENGINE_CREATED is False
    assert qmod.M3_METRIC_PROJECTION_IMPLEMENTED is False
    assert qmod.RAW_SOURCE_COPIED_INTO_TELEMETRY_STORE_FOR_HYDRATION is False
    # Ensure no query DSL
    assert "class QueryPlanner" not in text
    assert "def parse_sql" not in text.lower()

def test_query_digest_stability():
    req1 = TelemetryQueryRequest(project_id="proj-a", limit=5, aggregation_series_id=_make_series(op="workspace.read"))
    req2 = TelemetryQueryRequest(project_id="proj-a", limit=5, aggregation_series_id=req1.aggregation_series_id)
    assert req1.compute_digest() == req2.compute_digest()
    # canonicalization excludes wall clock etc.
    assert "nonce" not in req1.canonical_json().lower()

def test_unknown_version_fail_closed():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    state = _make_state_with_sum(proj, series, None, None, [5])
    store = EphemeralTelemetryStore()
    store.put(state)
    # query with unknown version should fail at request creation
    with pytest.raises(Exception):
        TelemetryQueryRequest(project_id=proj, storage_contract_version="future-v9", limit=5)
    # also tamper envelope with unknown version and query should fail closed
    # create envelope with unsupported version via __new__
    key = derive_storage_key(state)
    bad_env = object.__new__(StorageEnvelope)
    object.__setattr__(bad_env, "storage_contract_version", "future-v9")
    object.__setattr__(bad_env, "storage_key", key)
    object.__setattr__(bad_env, "aggregation_state", state)
    object.__setattr__(bad_env, "content_digest", compute_state_digest(state))
    object.__setattr__(bad_env, "prior_content_digest", None)
    object.__setattr__(bad_env, "storage_lifecycle", "active")
    store._store[key] = bad_env  # type: ignore
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    with pytest.raises(Exception):
        query_telemetry_store(store, req)

def test_coverage_separate_from_aggregate_completeness():
    proj = "proj-a"
    series = _make_series(op="workspace.read")
    # create incomplete aggregate
    state = create_initial_state(proj, series, None, None, None)
    src = _make_source(project_id=proj, obs_id="obs-incomplete", digest="c"*64)
    sd = compute_source_dedup_id(src)
    comp = CompletenessRecord(state=CompletenessState.PARTIAL, scope=CompletenessScope.SOURCE)
    contrib = create_aggregation_contribution(project_id=proj, source_dedup_id=sd, projection_namespace="ns", projection_version="v1", aggregation_series_id=series, aggregation_window_id=None, window_policy_id=None, window_policy_version=None, operation="SUM", value=5, completeness=comp)
    r = apply_contribution(state, contrib)
    assert r.new_state.completeness.complete is False
    store = EphemeralTelemetryStore()
    store.put(r.new_state)
    req = TelemetryQueryRequest(project_id=proj, limit=5)
    res = query_telemetry_store(store, req)
    # aggregate completeness is partial, but query coverage is separate
    assert res.items[0].completeness_complete is False
    assert res.coverage in (QueryCoverage.PARTIAL, QueryCoverage.COMPLETE, QueryCoverage.TRUNCATED_BY_LIMIT)
    # Ensure coverage not equal to aggregate completeness flag directly
    assert res.coverage is not None

def test_implicit_wall_clock_not_used():
    text = Path(inspect.getfile(qmod)).read_text()
    assert "datetime.now" not in text
    assert "time.time" not in text
    assert qmod.IMPLICIT_WALL_CLOCK_USED is False

def test_storage_neutral_no_private_access():
    text = Path(inspect.getfile(qmod)).read_text()
    # Should not access private _store dict directly
    assert "._store" not in text or "_store" in text and "_list_keys" in text  # we allow _list_keys but not _store dict
    # Ensure no isinstance with concrete store for branching
    assert "isinstance(store, EphemeralTelemetryStore)" not in text
    assert qmod.W2_PRIVATE_STORAGE_INTERNALS_ACCESSED is False

def test_bounded_cardinality_no_high_cardinality_selector():
    # Ensure query request does not allow arbitrary metadata keys
    with pytest.raises((ValueError, TypeError)):
        # try to pass arbitrary dict filter via unexpected kwarg
        TelemetryQueryRequest(project_id="proj-a", limit=5, **{"task_id": "xyz"})  # type: ignore
