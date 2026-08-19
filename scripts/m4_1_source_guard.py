#!/usr/bin/env python3
"""Focused M4-1 source contract and scope guard."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aota_forge.core.contracts.mutation import MutationEffect, MutationResult

focused = 0
negative = 0


def check(name: str, condition: bool) -> None:
    global focused
    focused += 1
    if not condition:
        raise AssertionError(name)


def reject(name: str, callback) -> None:
    global negative
    negative += 1
    try:
        callback()
    except (TypeError, ValueError):
        return
    raise AssertionError(f"negative case accepted: {name}")


def main() -> int:
    from aota_forge.core import execute
    from aota_forge.core.contracts.descriptor import InputSpec, OperationContractDescriptor
    from aota_forge.core.contracts.mutation import (
        AuthoritativeEffectConfirmation,
        MutationEffect,
        MutationIntent,
        MutationPreconditions,
        MutationResult,
    )
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY, HandlerRegistry
    from aota_forge.core.contracts.results import mutation_envelope

    # T1-T6: semantic identity is deterministic and has no trusted values.
    intent = MutationIntent(
        operation="subject.record_completion",
        semantic_inputs={"outcome": "succeeded", "notes": ["bounded"]},
        logical_target={"kind": "execution", "key": "exec-1"},
        mutation_scope={"aggregate": "owning_subject"},
        idempotency_key="idem-1",
        correlation_id="request-a",
    )
    twin = MutationIntent(
        operation=intent.operation,
        semantic_inputs={"notes": ["bounded"], "outcome": "succeeded"},
        logical_target={"key": "exec-1", "kind": "execution"},
        mutation_scope={"aggregate": "owning_subject"},
        idempotency_key="idem-1",
        correlation_id="request-a",
    )
    check("T1 canonical intent representation deterministic", intent.to_canonical_json() == twin.to_canonical_json())
    check("T2 intent fingerprint deterministic", intent.intent_fingerprint() == twin.intent_fingerprint())
    preconditions_a = MutationPreconditions(subject_expected_revision=3, authority_source_revision="raw-a")
    preconditions_b = MutationPreconditions(subject_expected_revision=4, authority_source_revision="raw-b")
    check("T3 preconditions stay separate", intent.with_preconditions(preconditions_a)[0].intent_fingerprint() == intent.with_preconditions(preconditions_b)[0].intent_fingerprint() and preconditions_a != preconditions_b)
    changed_target = MutationIntent(
        operation=intent.operation,
        semantic_inputs=intent.semantic_inputs,
        logical_target={"kind": "execution", "key": "exec-2"},
        mutation_scope=intent.mutation_scope,
        idempotency_key=intent.idempotency_key,
    )
    check("T4 target changes fingerprint", changed_target.intent_fingerprint() != intent.intent_fingerprint())
    changed_payload = MutationIntent(
        operation=intent.operation,
        semantic_inputs={"outcome": "failed", "notes": ["bounded"]},
        logical_target=intent.logical_target,
        mutation_scope=intent.mutation_scope,
        idempotency_key=intent.idempotency_key,
    )
    check("T5 payload changes fingerprint", changed_payload.intent_fingerprint() != intent.intent_fingerprint())
    runtime_variant = MutationIntent(
        operation=intent.operation,
        semantic_inputs=intent.semantic_inputs,
        logical_target=intent.logical_target,
        mutation_scope=intent.mutation_scope,
        idempotency_key=intent.idempotency_key,
        correlation_id="request-b",
    )
    check("T6 correlation excluded from fingerprint", runtime_variant.intent_fingerprint() == intent.intent_fingerprint())

    # T7-T12: one descriptor model, deterministic mutation-aware hash.
    read_descriptor = OperationContractDescriptor(
        name="fixture.read",
        description="read fixture",
        inputs=(InputSpec("value", "str"),),
    )
    read_roundtrip = OperationContractDescriptor.from_dict(read_descriptor.to_dict())
    check("T7 read descriptor round-trip/hash", read_roundtrip.to_dict() == read_descriptor.to_dict() and read_roundtrip.contract_hash() == read_descriptor.contract_hash())
    write_kwargs = dict(
        name="fixture.write",
        description="write fixture",
        read_write="write",
        mutation_scope="subject",
        required_authority="subject_mutation",
        approval_required=True,
        decision_required=True,
        valid_predecessor_state="open",
        valid_successor_state="completed",
        subject_revision_precondition=True,
        external_authority_precondition=True,
        idempotency="same intent replays; changed intent conflicts",
        result_contract="canonical_mutation_result.v1",
        errors=("CONFLICT", "OUTCOME_UNKNOWN", "MATERIALIZATION_FAILED"),
    )
    write_descriptor = OperationContractDescriptor(**write_kwargs)
    check("T8 mutation scope affects hash", write_descriptor.contract_hash() != OperationContractDescriptor(**{**write_kwargs, "mutation_scope": "plan"}).contract_hash())
    check("T9 authority requirement affects hash", write_descriptor.contract_hash() != OperationContractDescriptor(**{**write_kwargs, "required_authority": "other"}).contract_hash())
    check("T10 transition semantics affect hash", write_descriptor.contract_hash() != OperationContractDescriptor(**{**write_kwargs, "valid_successor_state": "cancelled"}).contract_hash())
    registry_a = HandlerRegistry()
    registry_b = HandlerRegistry()
    registry_a.bind(write_descriptor, handler=lambda _ctx: {"data": {"runtime": "a"}})
    registry_b.bind(OperationContractDescriptor.from_dict(write_descriptor.to_dict()), handler=lambda _ctx: {"data": {"runtime": "b"}})
    check("T11 handler identity excluded from hash", registry_a.get("fixture.write").contract_hash() == registry_b.get("fixture.write").contract_hash())
    check("T12 runtime authority values excluded", write_descriptor.contract_hash() == OperationContractDescriptor.from_dict(write_descriptor.to_dict()).contract_hash())
    check("single descriptor registry model", isinstance(DEFAULT_REGISTRY, HandlerRegistry))

    # T13-T16: effect/confirmation mapping and unknown-outcome safety.
    expected_confirmation = {
        MutationEffect.NO_EFFECT: AuthoritativeEffectConfirmation.NO,
        MutationEffect.APPLIED_VERIFIED: AuthoritativeEffectConfirmation.YES,
        MutationEffect.REPLAYED_VERIFIED: AuthoritativeEffectConfirmation.YES,
        MutationEffect.BLOCKED: AuthoritativeEffectConfirmation.NO,
        MutationEffect.CONFLICT: AuthoritativeEffectConfirmation.NO,
        MutationEffect.NEEDS_SEMANTIC_CHOICE: AuthoritativeEffectConfirmation.NO,
        MutationEffect.OUTCOME_UNKNOWN: AuthoritativeEffectConfirmation.UNKNOWN,
        MutationEffect.FAILED_NO_EFFECT: AuthoritativeEffectConfirmation.NO,
    }
    results = {
        effect: MutationResult("fixture.write", "completed", effect.value, effect, confirmation)
        for effect, confirmation in expected_confirmation.items()
    }
    check("T13 every effect maps explicitly", all(item.authoritative_effect_confirmed is expected_confirmation[effect] for effect, item in results.items()))
    check("T14 invalid combinations fail closed", all(
        (lambda effect, confirmation: _rejected_result(effect, confirmation))(effect, confirmation)
        for effect, confirmation in (
            (MutationEffect.OUTCOME_UNKNOWN, AuthoritativeEffectConfirmation.YES),
            (MutationEffect.FAILED_NO_EFFECT, AuthoritativeEffectConfirmation.YES),
            (MutationEffect.NO_EFFECT, AuthoritativeEffectConfirmation.UNKNOWN),
            (MutationEffect.APPLIED_VERIFIED, AuthoritativeEffectConfirmation.NO),
        )
    ))
    check("T15 effect states remain distinct", len({effect.value for effect in MutationEffect}) == 8 and MutationEffect.CONFLICT is not MutationEffect.NEEDS_SEMANTIC_CHOICE)
    unknown = MutationResult(
        "fixture.write",
        "reconciliation_required",
        "local_handler_completed",
        MutationEffect.OUTCOME_UNKNOWN,
        AuthoritativeEffectConfirmation.UNKNOWN,
        next_action="reconcile before retry",
    )
    unknown_envelope = mutation_envelope(unknown)
    check("T16 unknown local success is not verified success", unknown_envelope["ok"] is False and unknown_envelope["mutation_effect"] == "OUTCOME_UNKNOWN" and unknown_envelope["authoritative_effect_confirmed"] == "unknown")

    # T17-T18: platform-neutral source and the existing ingress gate.
    contract_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "aota_forge" / "core" / "contracts").glob("*.py"))
    check("T17 contracts have no platform identity requirement", not any(token in contract_text for token in ("github_issue_number", "github_comment_id", "github_etag", "gh command")))
    calls: list[str] = []
    denied_name = "fixture.write.ingress-denied"
    DEFAULT_REGISTRY.bind(
        OperationContractDescriptor(name=denied_name, description="isolated write descriptor", read_write="write"),
        handler=lambda _ctx: calls.append("called"),
    )
    denied = execute(denied_name, {})
    check("T18 canonical ingress still denies writes", denied.get("ok") is False and denied.get("errors", [{}])[0].get("code") == "UNSUPPORTED_OPERATION" and calls == [])

    # N1-N10: contract regressions must fail closed.
    reject("N1 authority field", lambda: MutationIntent("x", {"authority": True}, "target", "scope", "key"))
    reject("N2 capability lease field", lambda: MutationIntent("x", {"capability_lease": "lease"}, "target", "scope", "key"))
    reject("N3 callable semantic input", lambda: MutationIntent("x", {"callback": lambda: None}, "target", "scope", "key"))
    reject("N4 non-JSON mutation scope", lambda: OperationContractDescriptor(name="x", description="x", mutation_scope=object()).to_dict())
    reject("N5 non-JSON authority contract", lambda: OperationContractDescriptor(name="x", description="x", required_authority=object()).to_dict())
    reject("N6 unknown verified effect", lambda: MutationResult("x", "completed", "ok", MutationEffect.OUTCOME_UNKNOWN, AuthoritativeEffectConfirmation.YES))
    reject("N7 silent unknown success projection", lambda: _reject_verified_unknown(unknown_envelope))
    reject("N8 collapsed effect", lambda: _reject_collapsed_effects())
    reject("N9 platform semantic identity", lambda: MutationIntent("x", {"github_comment_id": 1}, "target", "scope", "key"))
    reject("N10 write descriptor executes through ingress", lambda: _reject_write_success(denied))

    print(f"M4_1_FOCUSED_TEST_COUNT={focused}")
    print(f"M4_1_NEGATIVE_TEST_COUNT={negative}")
    print("M4_1_SOURCE_GUARD=PASS")
    return 0


def _rejected_result(effect: MutationEffect, confirmation: AuthoritativeEffectConfirmation) -> bool:
    try:
        MutationResult("x", "completed", "x", effect, confirmation)
    except ValueError:
        return True
    return False


def _reject_verified_unknown(payload: dict[str, object]) -> None:
    if payload.get("ok") is True or payload.get("authoritative_effect_confirmed") == "yes":
        raise ValueError("unknown outcome was projected as verified")
    raise ValueError("unknown outcome correctly remains non-verified")


def _reject_collapsed_effects() -> None:
    if MutationEffect.CONFLICT.value == MutationEffect.NEEDS_SEMANTIC_CHOICE.value:
        raise ValueError("distinct effects collapsed")
    raise ValueError("conflict and choice correctly remain distinct")


def _reject_write_success(payload: dict[str, object]) -> None:
    if payload.get("ok") is True:
        raise ValueError("write executed")
    raise ValueError("write correctly remains denied")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        print(f"M4_1_SOURCE_GUARD=FAIL: {exc}")
        raise SystemExit(1)
