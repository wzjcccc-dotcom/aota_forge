"""Production adapter proofs for the existing W2 runtime provenance contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aota_forge.adapters.host.process import read_receipt
from aota_forge.adapters.host.resources import deployment_receipt_reference_ref
from aota_forge.adapters.host.runtime_provenance import (
    build_runtime_provenance_projection,
    canonical_role_capabilities,
    evaluate_runtime_provenance,
    evaluate_live_runtime_provenance,
    evaluate_runtime_provenance_projection,
    runtime_subject,
)
import aota_forge.adapters.host.runtime_provenance as host_provenance
from aota_forge.core.resources.resolver import TrustedResourceConfig
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


def test_live_adapter_rebuilds_observations_and_returns_bounded_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = Path(__file__).resolve().parents[1]
    import subprocess

    commit = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD^{tree}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subject = runtime_subject(
        project_id="aota_forge",
        source_commit=commit,
        source_tree=tree,
        version="0.1.0",
    )
    runtime_root = tmp_path / "runtime-candidate-1"
    (runtime_root / "activation").mkdir(parents=True)
    (runtime_root / "xdg-config" / "opencode").mkdir(parents=True)
    (runtime_root / ".aota").mkdir()
    (runtime_root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    (runtime_root / ".aota" / "project.yaml").write_text(
        "project:\n  id: aota_forge\n", encoding="utf-8"
    )
    identity = {
        "source_commit": commit,
        "source_tree": tree,
        "source_archive_sha256": "4" * 64,
    }
    (runtime_root / "activation" / "runtime-source-identity.json").write_text(
        json.dumps(identity), encoding="utf-8"
    )
    config = {
        "mcp": {
            "aota": {
                "environment": {
                    "AOTA_FORGE_RUNTIME_PROJECT_ID": "aota_forge",
                    "AOTA_FORGE_RUNTIME_SOURCE_COMMIT": commit,
                    "AOTA_FORGE_RUNTIME_SOURCE_TREE": tree,
                    "AOTA_FORGE_RUNTIME_VERSION": "0.1.0",
                }
            }
        }
    }
    config_bytes = json.dumps(config).encode()
    (runtime_root / "activation" / "opencode.json").write_bytes(config_bytes)
    (runtime_root / "xdg-config" / "opencode" / "opencode.json").write_bytes(config_bytes)
    caps = canonical_role_capabilities()
    evidence = {}
    for stage in STAGES:
        evidence[stage] = provenance(
            stage,
            fingerprint=compute_source_fingerprint(subject),
            generation="live-generation",
        )
    projection = build_runtime_provenance_projection(
        runtime_id=runtime_root.name,
        operator_generation="live-generation",
        subjects={stage: dict(subject) for stage in STAGES},
        stage_provenance=evidence,
        stage_capabilities={stage: caps for stage in STAGES},
        required_capabilities=caps,
        evidence_refs={stage: f"operator-observation:{stage}" for stage in STAGES},
    )
    projection["process"] = {
        "pid": 321,
        "start_time": "122.500",
        "runtime_id": runtime_root.name,
    }
    monkeypatch.setattr(
        host_provenance,
        "_running_subject",
        lambda root, rid, pid: (
            dict(subject),
            {"pid": pid, "process_start_time": "123.000", "runtime_id": rid},
        ),
    )

    result = evaluate_live_runtime_provenance(
        projection,
        runtime_id=runtime_root.name,
        source_root=source_root,
        runtime_root=runtime_root,
        pid=321,
        now=200,
    )

    assert result["eligibility"]["eligible"] is True
    assert result["eligibility"]["provenance_valid"] is True
    assert result["eligibility"]["capability_parity"]["parity"] is True
    assert set(result["capability_sets"]) == set(STAGES)
    assert len(json.dumps(result, separators=(",", ":")).encode()) < 2800
    host_status = {
        "data": {
            "identity": {"env_keys_present": ["AOTA_FORGE_REPO_ROOT", "AOTA_FORGE_REGISTRY"]},
            "project": {
                "project_id": "aota_forge",
                "workspace_id": "aota_forge",
                "project_root": str(source_root),
                "fingerprint": "5" * 64,
            },
            "runtime_identity": {"name": "aota-forge", "version": "0.1.0", "kind": None},
            "process": {"running": True, "pid": 321, "start_time": "123.000"},
            "deployment_receipt": {
                "schema_version": 1,
                "runtime_provenance_ref": {
                    "resource_kind": "managed_deployment_receipt",
                    "resource_id": runtime_root.name,
                    "sha256": "6" * 64,
                },
            },
            "checks": {
                name: {"requested": True, "state": "available", "error_code": None, "message": None}
                for name in ("project", "runtime_identity", "process", "deployment_receipt")
            },
            "runtime_provenance": result,
        }
    }
    assert len(json.dumps(host_status, separators=(",", ":")).encode()) < 4096


def test_host_status_receipt_reference_is_bounded_and_durable(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    (receipts / "runtime-1").mkdir(parents=True)
    receipt = receipts / "runtime-1" / "deployment.json"
    receipt.write_text(
        json.dumps({"schema_version": 1, "runtime_provenance": projection()}),
        encoding="utf-8",
    )

    result = deployment_receipt_reference_ref(
        "runtime-1", TrustedResourceConfig({"managed_deployment_receipt": receipts})
    )

    assert result == {
        "schema_version": 1,
        "runtime_provenance_ref": {
            "resource_kind": "managed_deployment_receipt",
            "resource_id": "runtime-1",
            "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        },
    }
