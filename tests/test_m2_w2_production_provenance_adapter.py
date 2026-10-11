"""Production adapter proofs for the existing W2 runtime provenance contract."""

from __future__ import annotations

import json

import pytest

from aota_forge.adapters.host.process import read_receipt
from aota_forge.adapters.host.runtime_provenance import (
    build_runtime_provenance_projection,
    evaluate_runtime_provenance,
    evaluate_runtime_provenance_projection,
    runtime_subject,
)
from aota_forge.core.runtime.provenance import (
    REASON_CAPABILITY_PARITY,
    REASON_FINGERPRINT_PARITY,
    REASON_HISTORICAL_RUNTIME,
    REASON_STALE_RUNTIME,
    REASON_UNKNOWN_FINGERPRINT,
    REASON_UNTRUSTED_PROVENANCE,
    TrustedRuntimeProvenance,
    compute_source_fingerprint,
)

SUBJECT = runtime_subject(
    project_id="aota_forge",
    source_commit="1" * 40,
    source_tree="2" * 40,
    version="0.1.0",
)
CAPABILITIES = frozenset({"task-main:operations.list", "coder:workspace.edit"})
STAGES = ("source", "configured", "deployed", "running")


def provenance(stage: str, **overrides: object) -> TrustedRuntimeProvenance:
    value = {
        "source": "systemd-operator-lifecycle",
        "authority_ref": "systemd-unit:opencode-af-reference.service",
        "evidence_ref": f"operator-observation:{stage}:generation-7",
        "trusted": True,
        "active": True,
        "current": True,
        "historical": False,
        "stale": False,
        "status": "active",
        "fingerprint": compute_source_fingerprint(SUBJECT),
        "generation": "generation-7",
        "observed_at": 200,
    }
    value.update(overrides)
    return TrustedRuntimeProvenance(**value)


def projection(
    *,
    subjects: dict[str, dict[str, str]] | None = None,
    provenance_overrides: dict[str, dict[str, object]] | None = None,
    capability_overrides: dict[str, frozenset[str]] | None = None,
) -> dict[str, object]:
    selected_subjects = subjects or {stage: dict(SUBJECT) for stage in STAGES}
    selected_provenance = provenance_overrides or {}
    selected_capabilities = capability_overrides or {}
    return build_runtime_provenance_projection(
        runtime_id="runtime-candidate-1",
        operator_generation="generation-7",
        subjects=selected_subjects,
        stage_provenance={
            stage: provenance(stage, **selected_provenance.get(stage, {}))
            for stage in STAGES
        },
        stage_capabilities={
            stage: selected_capabilities.get(stage, CAPABILITIES)
            for stage in STAGES
        },
        required_capabilities=CAPABILITIES,
        evidence_refs={stage: f"operator-observation:{stage}" for stage in STAGES},
    )


def test_aligned_four_stage_subject_reconstructs_equal_fingerprints() -> None:
    receipt_projection = projection()

    result = evaluate_runtime_provenance_projection(receipt_projection, now=200)

    assert result["fingerprints"] == {
        "source_fingerprint": compute_source_fingerprint(SUBJECT),
        "configured_fingerprint": compute_source_fingerprint(SUBJECT),
        "deployed_fingerprint": compute_source_fingerprint(SUBJECT),
        "running_fingerprint": compute_source_fingerprint(SUBJECT),
    }
    assert result["eligibility"]["eligible"] is True
    assert result["eligibility"]["provenance_valid"] is True
    assert result["eligibility"]["capability_parity"]["parity"] is True
    assert receipt_projection["contract"]["fingerprints"] == result["fingerprints"]


@pytest.mark.parametrize("stage", ("configured", "deployed", "running"))
def test_stage_subject_drift_fails_through_existing_evaluator(stage: str) -> None:
    subjects = {name: dict(SUBJECT) for name in STAGES}
    subjects[stage]["source_tree"] = "3" * 40

    result = evaluate_runtime_provenance_projection(projection(subjects=subjects), now=200)

    assert result["eligibility"]["eligible"] is False
    assert REASON_FINGERPRINT_PARITY in result["eligibility"]["reason_codes"]


def test_untrusted_stage_provenance_fails_closed() -> None:
    result = evaluate_runtime_provenance_projection(
        projection(provenance_overrides={"running": {"trusted": False}}), now=200
    )

    assert result["eligibility"]["eligible"] is False
    assert REASON_UNTRUSTED_PROVENANCE in result["eligibility"]["reason_codes"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"stale": True, "current": False}, REASON_STALE_RUNTIME),
        ({"status": "historical", "historical": True}, REASON_HISTORICAL_RUNTIME),
    ],
)
def test_stale_and_historical_stage_evidence_fails_closed(
    overrides: dict[str, object], reason: str
) -> None:
    result = evaluate_runtime_provenance_projection(
        projection(provenance_overrides={"running": overrides}), now=200
    )

    assert result["eligibility"]["eligible"] is False
    assert reason in result["eligibility"]["reason_codes"]


def test_missing_running_stage_fails_closed() -> None:
    result = evaluate_runtime_provenance(
        {stage: dict(SUBJECT) for stage in STAGES[:-1]} | {"running": None},
        {stage: provenance(stage) for stage in STAGES[:-1]},
        required_capabilities=CAPABILITIES,
        capabilities={stage: CAPABILITIES for stage in STAGES[:-1]},
        now=200,
    )

    assert result["eligibility"]["eligible"] is False
    assert REASON_UNKNOWN_FINGERPRINT in result["eligibility"]["reason_codes"]


@pytest.mark.parametrize(
    "running_capabilities",
    [
        frozenset({"task-main:operations.list"}),
        CAPABILITIES | {"reviewer:test.run"},
    ],
)
def test_running_capability_missing_or_extra_fails_closed(
    running_capabilities: frozenset[str],
) -> None:
    result = evaluate_runtime_provenance_projection(
        projection(), running_capabilities=running_capabilities, now=200
    )

    assert result["eligibility"]["eligible"] is False
    assert REASON_CAPABILITY_PARITY in result["eligibility"]["reason_codes"]


def test_schema_version_one_minimal_and_additive_receipts_remain_readable(
    tmp_path,
) -> None:
    minimal = tmp_path / "minimal.json"
    minimal.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    additive = tmp_path / "additive.json"
    additive.write_text(
        json.dumps({"schema_version": 1, "runtime_provenance": projection()}),
        encoding="utf-8",
    )

    assert read_receipt(minimal) == {"schema_version": 1}
    loaded = read_receipt(additive)
    assert loaded["schema_version"] == 1
    assert loaded["runtime_provenance"]["stage_evidence"]["source"]["stage"] == "source"
    assert evaluate_runtime_provenance_projection(
        loaded["runtime_provenance"], now=200
    )["eligibility"]["eligible"] is True
