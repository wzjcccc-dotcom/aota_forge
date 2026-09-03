"""S5 M1 W1 Context Budget & Rollover Decision Contract — focused proof.

Covers §34 required groups A-L plus §35-38 adversarial firewalls,
and §9/§28-29 authority/serialization / canonicalization reuse.

Production surface:
  aota_forge/work_plane/context_lifecycle.py

Invariants proven:
  - typed immutable contract, strict field validation, unknown fields
    fail closed, canonical serialization deterministic (A)
  - budget dimensions optional/independent, non-configured not authority,
    negative/bool/float/numeric-string rejected (B)
  - within-budget case (C), exact boundary not exceeded (D),
    hard exceedance → REQUIRED (E), missing evidence → UNDETERMINED (F)
  - Milestone boundary → RECOMMENDED not forced (G),
    explicit lifecycle request → RECOMMENDED (H),
    continuity-risk → RECOMMENDED (I), no S4 risk engine (I)
  - precedence: REQUIRED wins over RECOMMENDED (J)
  - authority-negative: not operation authority / Milestone approval /
    retry / repair / side-effect / workflow disposition (K, §25-26)
  - agent-neutral: no provider/model/session id required (L, §27)
  - model-tokenizer authority attack fails closed (§35)
  - arbitrary metadata attack fails closed (§36)
  - workflow authority firewall (§37)
  - runtime surface absent (§38)
  - bootstrap reuse without duplicate generic framework (§9)
  - deterministic serialization via canonical_json (§28-29)
  - exact thresholds deferred (§11)
"""

from __future__ import annotations

import pathlib
import hashlib
import pytest

from aota_forge.core.contracts.canonical import canonical_json
import aota_forge.work_plane.context_lifecycle as cl_mod
from aota_forge.work_plane.context_lifecycle import (
    ContextBudget,
    ContextUsage,
    RolloverDecision,
    RolloverDisposition,
    evaluate_rollover_decision,
)

# Helpers

def _budget(**kwargs) -> ContextBudget:
    return ContextBudget(**kwargs)

def _usage(**kwargs) -> ContextUsage:
    return ContextUsage(**kwargs)

# ---------------------------------------------------------------------------
# A. Contract shape
# ---------------------------------------------------------------------------

def test_a01_typed_immutable_contract():
    b = _budget(max_canonical_bytes=1000, max_component_count=10)
    assert isinstance(b, ContextBudget)
    # frozen
    with pytest.raises((AttributeError, TypeError)):
        b.max_canonical_bytes = 999  # type: ignore
    u = _usage(canonical_bytes=500)
    with pytest.raises((AttributeError, TypeError)):
        u.canonical_bytes = 123  # type: ignore
    d = RolloverDecision(disposition=RolloverDisposition.WITHIN_BUDGET)
    with pytest.raises((AttributeError, TypeError)):
        d.disposition = RolloverDisposition.UNDETERMINED  # type: ignore


def test_a02_strict_field_validation_unknown_fields_fail_closed():
    with pytest.raises((ValueError, TypeError)):
        ContextBudget.from_dict({"max_canonical_bytes": 100, "unknown": 1})
    with pytest.raises((ValueError, TypeError)):
        ContextUsage.from_dict({"canonical_bytes": 100, "extra": 5})
    with pytest.raises((ValueError, TypeError)):
        RolloverDecision.from_dict({"disposition": "WITHIN_BUDGET", "extra": True})
    # missing required field in decision
    with pytest.raises((ValueError, TypeError)):
        RolloverDecision.from_dict({})


def test_a03_canonical_serialization_deterministic():
    b1 = _budget(max_canonical_bytes=1024, max_component_count=8)
    b2 = _budget(max_component_count=8, max_canonical_bytes=1024)
    assert b1.canonical_dict() == b2.canonical_dict()
    assert b1.canonical_json() == b2.canonical_json()
    assert b1.canonical_json() == canonical_json(b1.canonical_dict())
    # usage deterministic
    u1 = _usage(canonical_bytes=10, component_count=2)
    u2 = _usage(component_count=2, canonical_bytes=10)
    assert u1.canonical_json() == u2.canonical_json()
    # decision deterministic
    d1 = RolloverDecision(disposition="WITHIN_BUDGET")
    d2 = RolloverDecision.from_dict({"disposition": "WITHIN_BUDGET"})
    assert d1.canonical_json() == d2.canonical_json()
    assert d1.canonical_json() == canonical_json({"disposition": "WITHIN_BUDGET"})
    # roundtrip
    restored = ContextBudget.from_dict(b1.to_dict())
    assert restored == b1
    assert restored.canonical_json() == b1.canonical_json()


def test_a04_unknown_fields_fail_closed_via_constructor_kwargs():
    # from_dict must reject model-token etc; also direct unknown kw via dict
    with pytest.raises((ValueError, TypeError)):
        ContextBudget.from_dict({"max_canonical_bytes": 100, "model_token_count": 999})
    with pytest.raises((ValueError, TypeError)):
        ContextUsage.from_dict({"canonical_bytes": 100, "provider_context_window": 8192})


# ---------------------------------------------------------------------------
# B. Budget dimensions
# ---------------------------------------------------------------------------

def test_b01_optional_configured_dimensions_work_independently():
    # only one dimension configured, others not authority
    b = _budget(max_canonical_bytes=1000)
    u_ok = _usage(canonical_bytes=999)
    dec = evaluate_rollover_decision(b, u_ok)
    assert dec.disposition == RolloverDisposition.WITHIN_BUDGET
    # same budget, different dimension not checked → still within budget
    u_other_large = _usage(canonical_bytes=10, component_count=9999)
    # component_count not configured, large value must not trigger REQUIRED
    dec2 = evaluate_rollover_decision(b, u_other_large)
    assert dec2.disposition == RolloverDisposition.WITHIN_BUDGET
    # configure second dimension
    b2 = _budget(max_canonical_bytes=1000, max_component_count=5)
    u2 = _usage(canonical_bytes=500, component_count=3)
    assert evaluate_rollover_decision(b2, u2).disposition == RolloverDisposition.WITHIN_BUDGET


def test_b02_non_configured_dimension_does_not_become_implicit_authority():
    b = _budget(max_canonical_bytes=100)
    # usage has huge values in non-configured dimensions, must stay WITHIN
    u = _usage(canonical_bytes=50, component_count=99999, eager_materialized_bytes=99999, progressive_ref_count=99999)
    dec = evaluate_rollover_decision(b, u)
    assert dec.disposition == RolloverDisposition.WITHIN_BUDGET
    # empty budget (no dimensions) never forces REQUIRED solely from usage
    b_empty = _budget()
    u_any = _usage(canonical_bytes=999999, component_count=999999)
    assert evaluate_rollover_decision(b_empty, u_any).disposition == RolloverDisposition.WITHIN_BUDGET


def test_b03_negative_values_rejected():
    with pytest.raises((ValueError, TypeError)):
        _budget(max_canonical_bytes=-1)
    with pytest.raises((ValueError, TypeError)):
        _budget(max_component_count=-5)
    with pytest.raises((ValueError, TypeError)):
        _usage(canonical_bytes=-10)
    with pytest.raises((ValueError, TypeError)):
        _usage(component_count=-1)


def test_b04_bool_as_int_rejected():
    with pytest.raises((TypeError, ValueError)):
        _budget(max_canonical_bytes=True)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _budget(max_component_count=False)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _usage(canonical_bytes=True)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _usage(component_count=False)  # type: ignore


def test_b05_float_rejected():
    with pytest.raises((TypeError, ValueError)):
        _budget(max_canonical_bytes=1.5)  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _usage(canonical_bytes=2.0)  # type: ignore


def test_b06_numeric_string_rejected():
    with pytest.raises((TypeError, ValueError)):
        ContextBudget.from_dict({"max_canonical_bytes": "100"})  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        ContextUsage.from_dict({"canonical_bytes": "100"})  # type: ignore
    with pytest.raises((TypeError, ValueError)):
        _budget(max_canonical_bytes="100")  # type: ignore


def test_b07_hard_cap_rejected():
    # pathological representation beyond hard cap must fail closed
    with pytest.raises((ValueError, TypeError)):
        _budget(max_canonical_bytes=cl_mod.MAX_CONTEXT_CANONICAL_BYTES_HARD + 1)
    with pytest.raises((ValueError, TypeError)):
        _usage(canonical_bytes=cl_mod.MAX_CONTEXT_CANONICAL_BYTES_HARD + 1)


# ---------------------------------------------------------------------------
# C. Within budget
# ---------------------------------------------------------------------------

def test_c01_within_budget_all_observed_le_no_trigger():
    b = _budget(max_canonical_bytes=1000, max_component_count=10, max_eager_materialized_bytes=5000, max_progressive_ref_count=20)
    u = _usage(canonical_bytes=500, component_count=5, eager_materialized_bytes=2000, progressive_ref_count=10)
    dec = evaluate_rollover_decision(b, u)
    assert dec.disposition == RolloverDisposition.WITHIN_BUDGET
    # also with triggers false explicitly
    dec2 = evaluate_rollover_decision(b, u, milestone_boundary=False, explicit_rollover_requested=False, continuity_risk=False)
    assert dec2.disposition == RolloverDisposition.WITHIN_BUDGET


# ---------------------------------------------------------------------------
# D. Exact boundary
# ---------------------------------------------------------------------------

def test_d01_exact_boundary_not_exceeded():
    b = _budget(max_canonical_bytes=1000, max_component_count=10)
    u = _usage(canonical_bytes=1000, component_count=10)
    dec = evaluate_rollover_decision(b, u)
    assert dec.disposition == RolloverDisposition.WITHIN_BUDGET
    # one at boundary, one below
    u2 = _usage(canonical_bytes=1000, component_count=5)
    assert evaluate_rollover_decision(b, u2).disposition == RolloverDisposition.WITHIN_BUDGET


# ---------------------------------------------------------------------------
# E. Hard exceedance
# ---------------------------------------------------------------------------

def test_e01_hard_exceedance_any_dimension_routes_required():
    b = _budget(max_canonical_bytes=100, max_component_count=5, max_eager_materialized_bytes=1000, max_progressive_ref_count=10)
    # exceed canonical_bytes
    assert evaluate_rollover_decision(b, _usage(canonical_bytes=101, component_count=1, eager_materialized_bytes=500, progressive_ref_count=5)).disposition == RolloverDisposition.ROLLOVER_REQUIRED
    # exceed component_count
    assert evaluate_rollover_decision(b, _usage(canonical_bytes=50, component_count=6, eager_materialized_bytes=500, progressive_ref_count=5)).disposition == RolloverDisposition.ROLLOVER_REQUIRED
    # exceed eager
    assert evaluate_rollover_decision(b, _usage(canonical_bytes=50, component_count=1, eager_materialized_bytes=1001, progressive_ref_count=5)).disposition == RolloverDisposition.ROLLOVER_REQUIRED
    # exceed progressive
    assert evaluate_rollover_decision(b, _usage(canonical_bytes=50, component_count=1, eager_materialized_bytes=500, progressive_ref_count=11)).disposition == RolloverDisposition.ROLLOVER_REQUIRED

def test_e02_hard_exceedance_is_not_execution_authority():
    assert cl_mod.ROLLOVER_REQUIRED_IS_ROLLOVER_EXECUTION_AUTHORITY is False
    b = _budget(max_canonical_bytes=10)
    u = _usage(canonical_bytes=20)
    dec = evaluate_rollover_decision(b, u)
    assert dec.disposition == RolloverDisposition.ROLLOVER_REQUIRED
    # disposition object must not grant authority fields
    assert not hasattr(dec, "is_authority")
    assert not hasattr(dec, "authority")


# ---------------------------------------------------------------------------
# F. Missing evidence
# ---------------------------------------------------------------------------

def test_f01_missing_required_dimension_routes_undetermined():
    b = _budget(max_canonical_bytes=100, max_component_count=10)
    # missing canonical_bytes evidence
    u_missing_one = _usage(component_count=5)  # canonical missing
    assert evaluate_rollover_decision(b, u_missing_one).disposition == RolloverDisposition.UNDETERMINED
    # missing component_count
    u_missing_two = _usage(canonical_bytes=50)
    assert evaluate_rollover_decision(b, u_missing_two).disposition == RolloverDisposition.UNDETERMINED
    # usage None entirely when budget configured
    assert evaluate_rollover_decision(b, None).disposition == RolloverDisposition.UNDETERMINED
    # not zero, not within_budget
    assert evaluate_rollover_decision(b, u_missing_one).disposition != RolloverDisposition.WITHIN_BUDGET


def test_f02_missing_evidence_fail_closed_not_within():
    assert cl_mod.MISSING_CONTEXT_BUDGET_EVIDENCE_IS_WITHIN_BUDGET is False
    b = _budget(max_eager_materialized_bytes=1000)
    u = _usage(canonical_bytes=10)  # eager missing
    dec = evaluate_rollover_decision(b, u)
    assert dec.disposition == RolloverDisposition.UNDETERMINED


# ---------------------------------------------------------------------------
# G. Milestone boundary
# ---------------------------------------------------------------------------

def test_g01_milestone_boundary_within_budget_recommended_not_forced():
    assert cl_mod.MILESTONE_DEFAULT_ROLLOVER_SUPPORTED is True
    assert cl_mod.MILESTONE_BOUNDARY_ALWAYS_FORCES_ROLLOVER is False
    b = _budget(max_canonical_bytes=1000)
    u = _usage(canonical_bytes=500)
    dec = evaluate_rollover_decision(b, u, milestone_boundary=True)
    assert dec.disposition == RolloverDisposition.ROLLOVER_RECOMMENDED
    # not REQUIRED and not execution
    assert dec.disposition != RolloverDisposition.ROLLOVER_REQUIRED
    # Must not automatically execute rollover — disposition is evidence only
    assert cl_mod.CONTEXT_ROLLOVER_IS_WORKFLOW_DISPOSITION is False


def test_g02_milestone_boundary_without_budget_also_recommended():
    b_empty = _budget()
    dec = evaluate_rollover_decision(b_empty, None, milestone_boundary=True)
    assert dec.disposition == RolloverDisposition.ROLLOVER_RECOMMENDED


# ---------------------------------------------------------------------------
# H. Explicit lifecycle request
# ---------------------------------------------------------------------------

def test_h01_explicit_rollover_request_within_budget_recommended():
    b = _budget(max_canonical_bytes=1000)
    u = _usage(canonical_bytes=10)
    dec = evaluate_rollover_decision(b, u, explicit_rollover_requested=True)
    assert dec.disposition == RolloverDisposition.ROLLOVER_RECOMMENDED
    assert cl_mod.TASK_MAIN_ROLLOVER_REQUEST_IS_OPERATION_AUTHORITY is False
    # explicit request does not grant operation authority fields
    assert not hasattr(dec, "is_authority")


# ---------------------------------------------------------------------------
# I. Continuity-risk evidence
# ---------------------------------------------------------------------------

def test_i01_continuity_risk_within_budget_recommended():
    b = _budget(max_canonical_bytes=1000)
    u = _usage(canonical_bytes=10)
    dec = evaluate_rollover_decision(b, u, continuity_risk=True)
    assert dec.disposition == RolloverDisposition.ROLLOVER_RECOMMENDED
    assert cl_mod.NEW_CONTEXT_RISK_ENGINE_CREATED is False
    # Ensure module did not duplicate S4 risk model (no risk engine class)
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    assert "class Risk" not in src
    assert "RiskEngine" not in src


# ---------------------------------------------------------------------------
# J. Precedence
# ---------------------------------------------------------------------------

def test_j01_required_wins_over_recommended():
    b = _budget(max_canonical_bytes=100, max_component_count=10)
    u = _usage(canonical_bytes=200, component_count=5)  # exceeds
    dec = evaluate_rollover_decision(b, u, milestone_boundary=True, explicit_rollover_requested=True, continuity_risk=True)
    assert dec.disposition == RolloverDisposition.ROLLOVER_REQUIRED
    # also precedence invalid > required
    b2 = _budget(max_canonical_bytes=100, max_component_count=10)
    # usage missing for one dimension but another exceeds; invalid should win (UNDETERMINED)
    u_missing_and_exceed = _usage(canonical_bytes=200)  # missing component_count
    dec2 = evaluate_rollover_decision(b2, u_missing_and_exceed, milestone_boundary=True)
    assert dec2.disposition == RolloverDisposition.UNDETERMINED


def test_j02_undetermined_wins_over_all():
    b = _budget(max_canonical_bytes=100)
    u = _usage()  # missing all but budget requires it
    dec = evaluate_rollover_decision(b, u, milestone_boundary=True, explicit_rollover_requested=True, continuity_risk=True)
    assert dec.disposition == RolloverDisposition.UNDETERMINED


# ---------------------------------------------------------------------------
# K. Authority-negative
# ---------------------------------------------------------------------------

def test_k01_budget_disposition_not_authority():
    assert cl_mod.CONTEXT_BUDGET_IS_OPERATION_AUTHORITY is False
    assert cl_mod.CONTEXT_USAGE_IS_OPERATION_AUTHORITY is False
    assert cl_mod.BUDGET_DISPOSITION_IS_OPERATION_AUTHORITY is False
    assert cl_mod.BUDGET_DISPOSITION_IS_MILESTONE_APPROVAL is False
    assert cl_mod.BUDGET_DISPOSITION_IS_WORK_ITEM_COMPLETION_AUTHORITY is False
    assert cl_mod.ROLLOVER_REQUIRED_IS_ROLLOVER_EXECUTION_AUTHORITY is False
    assert cl_mod.CONTEXT_ROLLOVER_IS_WORKFLOW_DISPOSITION is False
    assert cl_mod.ROLLOVER_DOES_NOT_RESET_WORKFLOW_AUTHORITY is True
    assert cl_mod.ROLLOVER_DOES_NOT_MINT_OPERATION_AUTHORITY is True
    assert cl_mod.ROLLOVER_DECISION_IS_RETRY_PERMISSION is False
    assert cl_mod.ROLLOVER_DECISION_IS_REPAIR_AUTHORITY is False
    assert cl_mod.ROLLOVER_DECISION_IS_SIDE_EFFECT_REPLAY_AUTHORITY is False
    # objects cannot masquerade as authority
    b = _budget(max_canonical_bytes=100)
    u = _usage(canonical_bytes=200)
    dec = evaluate_rollover_decision(b, u)
    # No authority attribute
    assert not hasattr(b, "is_authority")
    assert not hasattr(u, "is_authority")
    assert not hasattr(dec, "is_authority")
    assert not hasattr(dec, "authority")
    # Workflow authority not minted
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    assert "is_authority" not in src.lower() or src.lower().count("is_authority") < 5  # allow constant names only


# ---------------------------------------------------------------------------
# L. Agent-neutrality
# ---------------------------------------------------------------------------

def test_l01_agent_neutral_no_session_ids_required():
    assert cl_mod.CONTEXT_LIFECYCLE_CONTRACT_AGENT_NEUTRAL is True
    assert cl_mod.MODEL_NATIVE_SESSION_ID_IS_SEMANTIC_AUTHORITY is False
    # Construction without any model/provider/session identifiers
    b = _budget(max_canonical_bytes=1000)
    u = _usage(canonical_bytes=10)
    dec = evaluate_rollover_decision(b, u)
    d = dec.canonical_dict()
    assert "session" not in str(d).lower()
    assert "model" not in str(d).lower()
    assert "provider" not in str(d).lower()
    # from_dict does not require those fields
    b2 = ContextBudget.from_dict({"max_canonical_bytes": 100})
    assert b2.max_canonical_bytes == 100
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8").lower()
    assert "hermes session id" not in src
    assert "openai" not in src or "openai_token" not in src  # no openai token authority


# ---------------------------------------------------------------------------
# §35 Model tokenizer attack
# ---------------------------------------------------------------------------

def test_model_tokenizer_authority_attack_fails_closed():
    assert cl_mod.MODEL_TOKENIZER_IS_CONTEXT_BUDGET_AUTHORITY is False
    # Attempt to feed model_token fields via dict
    for payload in [
        {"max_canonical_bytes": 100, "model_token_count": 999},
        {"max_canonical_bytes": 100, "model_tokens": 500},
        {"max_canonical_bytes": 100, "token_count": 500},
        {"max_canonical_bytes": 100, "provider_context_window": 8192},
        {"max_canonical_bytes": 100, "model_name": "gpt-4"},
    ]:
        with pytest.raises((ValueError, TypeError)):
            ContextBudget.from_dict(payload)  # type: ignore
    for payload in [
        {"canonical_bytes": 100, "model_token_count": 999},
        {"canonical_bytes": 100, "token_count": 123},
    ]:
        with pytest.raises((ValueError, TypeError)):
            ContextUsage.from_dict(payload)  # type: ignore
    # Also direct construction with extra kw must fail via from_dict path
    with pytest.raises((ValueError, TypeError)):
        ContextBudget.from_dict({"max_canonical_bytes": 100, "openai_token_count": 1})  # type: ignore


# ---------------------------------------------------------------------------
# §36 Arbitrary metadata attack
# ---------------------------------------------------------------------------

def test_arbitrary_metadata_attack_fails_closed():
    assert cl_mod.ARBITRARY_CONTEXT_METADATA_BAG_ALLOWED is False
    with pytest.raises((ValueError, TypeError)):
        ContextBudget.from_dict({"max_canonical_bytes": 100, "metadata": {"approved": True}})  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        ContextUsage.from_dict({"canonical_bytes": 100, "metadata": {"x": 1}})  # type: ignore
    with pytest.raises((ValueError, TypeError)):
        RolloverDecision.from_dict({"disposition": "WITHIN_BUDGET", "metadata": {"approved": True}})  # type: ignore
    # Unknown field at top level must also fail
    with pytest.raises((ValueError, TypeError)):
        ContextBudget.from_dict({"max_canonical_bytes": 100, "approved": True})  # type: ignore


# ---------------------------------------------------------------------------
# §37 Workflow authority attack
# ---------------------------------------------------------------------------

def test_workflow_authority_firewall():
    # ROLLOVER_REQUIRED must not be usable as Milestone approved / Git authorized etc.
    b = _budget(max_canonical_bytes=10)
    u = _usage(canonical_bytes=20)
    dec = evaluate_rollover_decision(b, u)
    assert dec.disposition == RolloverDisposition.ROLLOVER_REQUIRED
    # Assert flags
    assert cl_mod.BUDGET_DISPOSITION_IS_MILESTONE_APPROVAL is False
    assert cl_mod.BUDGET_DISPOSITION_IS_WORK_ITEM_COMPLETION_AUTHORITY is False
    assert cl_mod.CONTEXT_ROLLOVER_IS_WORKFLOW_DISPOSITION is False
    # Decision dict does not contain approval fields
    d = dec.canonical_dict()
    assert "approved" not in d
    assert "milestone" not in str(d).lower()
    assert "retry" not in str(d).lower()
    assert "git" not in str(d).lower()


# ---------------------------------------------------------------------------
# §38 Runtime surface attack
# ---------------------------------------------------------------------------

def test_runtime_surface_absent():
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    lower = src.lower()
    # Must not import or instantiate runtime mechanisms
    for token in ["import threading", "import subprocess", "import asyncio", "from threading", "from subprocess"]:
        assert token not in lower, f"prohibited import {token} found"
    # No checkpoint store / session manager class definitions
    assert "class SessionManager" not in src
    assert "class CheckpointStore" not in src
    assert "class RecoveryEngine" not in src
    assert "class SessionCheckpoint" not in src
    assert "class WorkingTruthProjection" not in src
    assert "class TaskMainRuntime" not in src
    assert "class ContextRuntime" not in src
    assert "class RolloverDaemon" not in src
    # No flags indicating runtime created
    assert cl_mod.W1_RUNTIME_CREATED is False
    assert cl_mod.NEW_SESSION_RUNTIME_REQUIRED_FOR_M1 is False
    assert cl_mod.NEW_SESSION_MANAGER_REQUIRED_FOR_M1 is False
    assert cl_mod.NEW_CHECKPOINT_STORE_REQUIRED_FOR_M1 is False
    assert cl_mod.NEW_RECOVERY_ENGINE_REQUIRED_FOR_M1 is False


# ---------------------------------------------------------------------------
# Additional: bootstrap reuse, deferral, deterministic, mid-milestone, etc.
# ---------------------------------------------------------------------------

def test_bootstrap_reuse_without_duplicate_framework():
    assert cl_mod.EXISTING_BOOTSTRAP_BUDGET_SEMANTICS_REUSED is True
    assert cl_mod.DUPLICATE_GENERIC_BUDGET_FRAMEWORK_CREATED is False
    assert cl_mod.EXISTING_CANONICALIZATION_REUSED is True
    assert cl_mod.NEW_CANONICALIZATION_FRAMEWORK_CREATED is False
    assert cl_mod.NEW_GENERIC_POLICY_FRAMEWORK_CREATED is False
    # Module reuses canonical_json, not parallel framework
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    assert "from aota_forge.core.contracts.canonical import canonical_json" in src
    assert "canonicalize" not in src or "def canonicalize" not in src  # must not define new canonicalizer
    # No subclass duplication of BootstrapBudget
    assert "class BootstrapBudget" not in src


def test_exact_thresholds_deferred():
    assert cl_mod.EXACT_CONTEXT_BUDGET_LIMIT_DEFERRED is True
    # Production contract accepts caller-governed bounds; test fixtures use bounded values
    b = _budget(max_canonical_bytes=1234, max_component_count=42)
    assert b.max_canonical_bytes == 1234
    # Different fixture numbers must be possible without changing hard cap
    b2 = _budget(max_canonical_bytes=9999)
    assert b2.max_canonical_bytes == 9999


def test_deterministic_evaluator_purity():
    assert cl_mod.ROLLOVER_DECISION_EVALUATOR_PURE is True
    b = _budget(max_canonical_bytes=100)
    u = _usage(canonical_bytes=50)
    dec1 = evaluate_rollover_decision(b, u, milestone_boundary=True)
    dec2 = evaluate_rollover_decision(b, u, milestone_boundary=True)
    assert dec1 == dec2
    assert dec1.canonical_json() == dec2.canonical_json()
    # No wall-clock or random
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    assert "time.time" not in src
    assert "random" not in src.lower() or "random" not in src.lower().split("import")[-1]
    # Pure: no filesystem writes
    assert "open(" not in src or src.count("open(") == 0  # allow no open


def test_mid_milestone_semantic_eligibility():
    assert cl_mod.MID_MILESTONE_ROLLOVER_SUPPORTED is True
    assert cl_mod.MID_MILESTONE_ROLLOVER_REQUIRES_PLAN_RESTART is False
    assert cl_mod.MID_MILESTONE_ROLLOVER_REQUIRES_WORK_ITEM_RESTART is False
    # Mid-milestone via explicit request within budget should be RECOMMENDED
    b = _budget(max_canonical_bytes=1000)
    u = _usage(canonical_bytes=10)
    dec = evaluate_rollover_decision(b, u, explicit_rollover_requested=True)
    assert dec.disposition == RolloverDisposition.ROLLOVER_RECOMMENDED
    # Not forced execution, not workflow reset


def test_disposition_vocabulary_distinct():
    vals = set(RolloverDisposition)
    assert len(vals) == 4
    assert RolloverDisposition.WITHIN_BUDGET != RolloverDisposition.ROLLOVER_RECOMMENDED
    assert RolloverDisposition.ROLLOVER_RECOMMENDED != RolloverDisposition.ROLLOVER_REQUIRED
    assert RolloverDisposition.UNDETERMINED != RolloverDisposition.WITHIN_BUDGET
    # string values match expected
    assert RolloverDisposition.WITHIN_BUDGET.value == "WITHIN_BUDGET"
    assert RolloverDisposition.UNDETERMINED.value == "UNDETERMINED"


def test_m1_m2_boundary_retained():
    assert cl_mod.W1_CONTRACT_ONLY is True
    assert cl_mod.M1_IMPLEMENT_ACTUAL_ROLLOVER is False
    assert cl_mod.M1_IMPLEMENT_ACTUAL_RECOVERY is False
    assert cl_mod.M2_OWNS_ROLLOVER_EXECUTION is True
    assert cl_mod.M1_M2_BOUNDARY_RETAINED is True
    assert cl_mod.SESSION_CHECKPOINT_IMPLEMENTED_BY_W1 is False
    assert cl_mod.WORKING_TRUTH_IMPLEMENTED_BY_W1 is False
    assert cl_mod.CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W1 is False


def test_shared_accepted_contract_not_modified():
    assert cl_mod.SHARED_ACCEPTED_CONTRACT_CHANGE_REQUIRED is False
    assert cl_mod.AGGREGATOR_CHANGE_REQUIRED_FOR_W1 is False
    # Ensure aggregator not modified
    agg = pathlib.Path(cl_mod.__file__).parent / "__init__.py"
    content = agg.read_text(encoding="utf-8")
    assert "context_lifecycle" not in content


def test_no_session_checkpoint_in_w1():
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    assert "class SessionCheckpoint" not in src
    assert "class WorkingTruthProjection" not in src
    assert "class ActiveTaskProjection" not in src
    # checkpoint digest identity etc not implemented
    assert "checkpoint digest identity" not in src.lower()


def test_no_context_provider_integration():
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    # Must not import or orchestrate ContextProvider
    lower = src.lower()
    assert "contextprovider" not in lower
    assert "contextrequest" not in lower
    assert "selective_hydration" not in lower or "selective_hydration" not in lower  # ensure not integrated
    assert cl_mod.CONTEXT_PROVIDER_INTEGRATION_STARTED_BY_W1 is False


def test_w2_scope_not_pulled_forward():
    assert cl_mod.W2_SCOPE_PULLED_FORWARD_BY_W1 is False
    src = pathlib.Path(cl_mod.__file__).read_text(encoding="utf-8")
    assert "SessionCheckpoint" not in src or "SESSION_CHECKPOINT_IMPLEMENTED_BY_W1" in src
    # Ensure no digest identity etc
    assert "checkpoint freshness evaluator" not in src.lower()


def test_authority_negative_constants():
    # Ensure all authority-negative flags present
    assert cl_mod.CONTEXT_BUDGET_IS_OPERATION_AUTHORITY is False
    assert cl_mod.BUDGET_DISPOSITION_IS_OPERATION_AUTHORITY is False
    assert cl_mod.ROLLOVER_DECISION_IS_RETRY_PERMISSION is False

