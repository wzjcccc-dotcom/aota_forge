"""Production observations adapted to the existing W2 runtime contract."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

from aota_forge.core.runtime.provenance import (
    RuntimeIdentityContract,
    RuntimeIdentityEvidence,
    RuntimeFingerprintSet,
    compute_source_fingerprint,
    evaluate_active_runtime,
)

RUNTIME_SUBJECT_FIELDS = ("project_id", "source_commit", "source_tree", "version")
RUNTIME_STAGES = ("source", "configured", "deployed", "running")
MAX_RUNTIME_IDENTITY_BYTES = 64 * 1024
MAX_CONFIG_BYTES = 256 * 1024
_GIT_IDENTITY = re.compile(r"^[0-9a-f]{40}$")


def runtime_subject(
    *, project_id: str, source_commit: str, source_tree: str, version: str
) -> dict[str, str]:
    """Bound the shared runtime subject to facts independently recoverable at each stage."""
    subject = {
        "project_id": project_id,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "version": version,
    }
    if any(not isinstance(value, str) or not value.strip() for value in subject.values()):
        raise ValueError("runtime subject fields must be non-empty strings")
    if len(project_id) > 128 or len(source_commit) != 40 or len(source_tree) != 40 or len(version) > 64:
        raise ValueError("runtime subject field exceeds its bounded identity form")
    if not _GIT_IDENTITY.fullmatch(source_commit) or not _GIT_IDENTITY.fullmatch(source_tree):
        raise ValueError("runtime source commit and tree must be hexadecimal Git identities")
    return subject


def canonical_role_capabilities() -> frozenset[str]:
    """Project existing role/operation exposure using its canonical owners."""
    from aota_forge.work_plane.af_roles import get_tool_surface_for_role
    from aota_forge.work_plane.roles import AgentWorkRole

    return frozenset(
        f"{role.value}:{operation}"
        for role in AgentWorkRole
        for operation in get_tool_surface_for_role(role).all_capability_names()
    )


def evaluate_runtime_provenance(
    subjects: Mapping[str, Mapping[str, str] | None],
    provenance: Mapping[str, Any],
    *,
    required_capabilities: Any,
    capabilities: Mapping[str, Any],
    expected_generation: str | int | None = None,
    now: Any = None,
    max_age_seconds: float | None = None,
) -> dict[str, Any]:
    """Reconstruct W2 stage evidence and invoke the existing eligibility evaluator."""
    stages: dict[str, RuntimeIdentityEvidence | None] = {}
    fingerprints: dict[str, str | None] = {}
    for stage in RUNTIME_STAGES:
        subject = subjects.get(stage)
        if subject is None:
            stages[stage] = None
            fingerprints[stage] = None
            continue
        if set(subject) != set(RUNTIME_SUBJECT_FIELDS):
            raise ValueError(f"{stage} runtime subject has a non-canonical field set")
        fingerprint = compute_source_fingerprint(runtime_subject(**subject))
        fingerprints[stage] = fingerprint
        stages[stage] = RuntimeIdentityEvidence(
            stage=stage,
            fingerprint=fingerprint,
            provenance=provenance.get(stage),
            capabilities=capabilities.get(stage),
        )

    contract = RuntimeIdentityContract(
        source=stages["source"],
        configured=stages["configured"],
        deployed=stages["deployed"],
        running=stages["running"],
        required_capabilities=required_capabilities,
        configured_capabilities=capabilities.get("configured"),
        deployed_capabilities=capabilities.get("deployed"),
        running_capabilities=capabilities.get("running"),
        expected_generation=expected_generation,
        max_age_seconds=max_age_seconds,
    )
    result = evaluate_active_runtime(contract, now=now)
    return {
        "fingerprints": RuntimeFingerprintSet(
            source=fingerprints["source"],
            configured=fingerprints["configured"],
            deployed=fingerprints["deployed"],
            running=fingerprints["running"],
        ).to_dict(),
        "stage_evidence": {
            stage: evidence.to_dict() if evidence is not None else None
            for stage, evidence in stages.items()
        },
        "eligibility": result.to_dict(),
    }


def evaluate_runtime_provenance_projection(
    projection: Mapping[str, Any], *, running_capabilities: Any = None, now: Any = None
) -> dict[str, Any]:
    """Consume a bounded receipt projection; the receipt itself is never a decision."""
    subjects = projection.get("subjects")
    evidence = projection.get("stage_evidence")
    if not isinstance(subjects, Mapping) or not isinstance(evidence, Mapping):
        return evaluate_runtime_provenance(
            {}, {}, required_capabilities=None, capabilities={}, now=now
        )

    provenance: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    for stage in RUNTIME_STAGES:
        item = evidence.get(stage)
        if not isinstance(item, Mapping):
            continue
        provenance[stage] = item.get("provenance")
        capabilities[stage] = item.get("capabilities")
    if running_capabilities is not None:
        capabilities["running"] = running_capabilities
    return evaluate_runtime_provenance(
        subjects,
        provenance,
        required_capabilities=projection.get("required_capabilities"),
        capabilities=capabilities,
        expected_generation=projection.get("operator_generation"),
        now=now,
    )


def build_runtime_provenance_projection(
    *,
    runtime_id: str,
    operator_generation: str | int,
    subjects: Mapping[str, Mapping[str, str]],
    stage_provenance: Mapping[str, Any],
    stage_capabilities: Mapping[str, Any],
    required_capabilities: Any,
    evidence_refs: Mapping[str, str],
) -> dict[str, Any]:
    """Serialize stage facts with the existing W2 evidence/provenance carriers."""
    evidence_objects: dict[str, RuntimeIdentityEvidence] = {}
    for stage in RUNTIME_STAGES:
        subject = runtime_subject(**subjects[stage])
        fingerprint = compute_source_fingerprint(subject)
        evidence_objects[stage] = RuntimeIdentityEvidence(
            stage=stage,
            fingerprint=fingerprint,
            provenance=stage_provenance[stage],
            capabilities=stage_capabilities[stage],
        )
    contract = RuntimeIdentityContract(
        source=evidence_objects["source"],
        configured=evidence_objects["configured"],
        deployed=evidence_objects["deployed"],
        running=evidence_objects["running"],
        required_capabilities=required_capabilities,
        configured_capabilities=stage_capabilities["configured"],
        deployed_capabilities=stage_capabilities["deployed"],
        running_capabilities=stage_capabilities["running"],
        expected_generation=operator_generation,
    )
    return {
        "runtime_id": runtime_id,
        "operator_generation": operator_generation,
        "subjects": {stage: dict(subjects[stage]) for stage in RUNTIME_STAGES},
        "contract": contract.to_dict(),
        "stage_evidence": {
            stage: evidence.to_dict() for stage, evidence in evidence_objects.items()
        },
        "required_capabilities": sorted(required_capabilities),
        "evidence_refs": {stage: evidence_refs[stage] for stage in RUNTIME_STAGES},
    }


def evaluate_live_runtime_provenance(
    projection: Mapping[str, Any],
    *,
    runtime_id: str,
    source_root: Path,
    runtime_root: Path,
    pid: int,
    now: Any = None,
) -> dict[str, Any]:
    """Rebuild all four stages from candidate source, activation, runtime, and live process facts."""
    recorded_subjects = projection.get("subjects")
    recorded_evidence = projection.get("stage_evidence")
    if (
        projection.get("runtime_id") != runtime_id
        or not isinstance(recorded_subjects, Mapping)
        or not isinstance(recorded_evidence, Mapping)
        or runtime_root.is_symlink()
        or not runtime_root.is_dir()
        or runtime_root.name != runtime_id
    ):
        return _incomplete_projection_result(now=now)

    source_record = recorded_subjects.get("source")
    if not isinstance(source_record, Mapping):
        return _incomplete_projection_result(now=now)
    source_commit = source_record.get("source_commit")
    if not isinstance(source_commit, str) or not _GIT_IDENTITY.fullmatch(source_commit):
        return _incomplete_projection_result(now=now)
    source_subject = _source_subject(source_root, source_commit)
    configured_subject, config_digest = _configured_subject(runtime_root)
    deployed_subject, deployment_refs = _deployed_subject(runtime_root)
    running_subject, process_ref = _running_subject(runtime_root, runtime_id, pid)
    recorded_process = projection.get("process")
    if (
        not isinstance(recorded_process, Mapping)
        or recorded_process.get("pid") != pid
        or recorded_process.get("runtime_id") != runtime_id
        or recorded_process.get("start_time") != process_ref.get("process_start_time")
        or not process_ref.get("process_start_time")
    ):
        running_subject = None
        process_ref = {}

    subjects = {
        "source": source_subject,
        "configured": configured_subject,
        "deployed": deployed_subject,
        "running": running_subject,
    }
    provenance: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    for stage in RUNTIME_STAGES:
        item = recorded_evidence.get(stage)
        if isinstance(item, Mapping):
            provenance[stage] = item.get("provenance")
            capabilities[stage] = item.get("capabilities")
    live_capabilities = canonical_role_capabilities()
    capabilities["running"] = live_capabilities if running_subject is not None else None
    result = evaluate_runtime_provenance(
        subjects,
        provenance,
        required_capabilities=projection.get("required_capabilities"),
        capabilities=capabilities,
        expected_generation=projection.get("operator_generation"),
        now=now,
    )
    decision = result["eligibility"]
    parity = decision["capability_parity"] or {}
    capability_refs = projection.get("capability_evidence_refs")
    if not isinstance(capability_refs, Mapping):
        capability_refs = {}
    stage_refs: dict[str, dict[str, Any]] = {}
    for stage in RUNTIME_STAGES:
        item = recorded_evidence.get(stage)
        stage_provenance = item.get("provenance") if isinstance(item, Mapping) else None
        stage_refs[stage] = {
            "authority_ref": stage_provenance.get("authority_ref")
            if isinstance(stage_provenance, Mapping)
            else None,
            "evidence_ref": stage_provenance.get("evidence_ref")
            if isinstance(stage_provenance, Mapping)
            else None,
            "capability_evidence_ref": capability_refs.get(stage),
            "observed": stage != "running" or running_subject is not None,
        }
    return {
        "runtime_id": runtime_id,
        "runtime_subject": source_subject,
        "fingerprints": result["fingerprints"],
        "eligibility": {
            "eligible": decision["eligible"],
            "reason_codes": decision["reason_codes"],
            "provenance_valid": decision["provenance_valid"],
            "external_evidence_present": decision["external_evidence_present"],
            "capability_parity": {
                "parity": parity.get("parity", False),
                "reason_codes": parity.get("reason_codes", []),
                "missing_count": len(parity.get("missing") or []),
                "unexpected_count": len(parity.get("unexpected") or []),
                "expected_count": len(parity.get("expected") or []),
                "observed_count": len(parity.get("observed") or []),
                "expected_sha256": compute_source_fingerprint(parity.get("expected") or []),
                "observed_sha256": compute_source_fingerprint(parity.get("observed") or []),
            },
        },
        "capability_sets": {
            stage: _capability_summary(
                capabilities.get(stage)
            )
            for stage in RUNTIME_STAGES
        },
        "stage_evidence_refs": stage_refs,
        "observations": {
            "source": {
                "evidence_ref": f"git:{source_subject['source_commit']}:{source_subject['source_tree']}"
                if source_subject
                else None,
            },
            "configured": {"configuration_sha256": config_digest},
            "deployed": deployment_refs,
            "running": process_ref,
        },
    }


def _incomplete_projection_result(*, now: Any) -> dict[str, Any]:
    return evaluate_runtime_provenance(
        {}, {}, required_capabilities=None, capabilities={}, now=now
    )


def _capability_summary(value: Any) -> dict[str, Any]:
    if value is None:
        return {"count": None, "sha256": None}
    if isinstance(value, str):
        names = [value]
    elif isinstance(value, Mapping):
        names = sorted(name for name, enabled in value.items() if enabled is True)
    else:
        names = sorted(set(value))
    return {"count": len(names), "sha256": compute_source_fingerprint(names)}


def _source_subject(source_root: Path, commit: str) -> dict[str, str] | None:
    try:
        tree = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", f"{commit}^{{tree}}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        version = subprocess.run(
            ["git", "-C", str(source_root), "show", f"{commit}:VERSION"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        manifest = subprocess.run(
            ["git", "-C", str(source_root), "show", f"{commit}:.aota/project.yaml"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        import yaml

        project_id = yaml.safe_load(manifest)["project"]["id"]
        return runtime_subject(
            project_id=project_id,
            source_commit=commit,
            source_tree=tree,
            version=version,
        )
    except (KeyError, TypeError, ValueError, yaml.YAMLError):
        return None


def _configured_subject(runtime_root: Path) -> tuple[dict[str, str] | None, str | None]:
    activation = runtime_root / "activation" / "opencode.json"
    rendered = runtime_root / "xdg-config" / "opencode" / "opencode.json"
    try:
        activation_bytes = _bounded_file(activation, MAX_CONFIG_BYTES)
        rendered_bytes = _bounded_file(rendered, MAX_CONFIG_BYTES)
        if activation_bytes != rendered_bytes:
            return None, None
        data = json.loads(activation_bytes)
        environment = data["mcp"]["aota"]["environment"]
        return (
            runtime_subject(
                project_id=environment["AOTA_FORGE_RUNTIME_PROJECT_ID"],
                source_commit=environment["AOTA_FORGE_RUNTIME_SOURCE_COMMIT"],
                source_tree=environment["AOTA_FORGE_RUNTIME_SOURCE_TREE"],
                version=environment["AOTA_FORGE_RUNTIME_VERSION"],
            ),
            hashlib.sha256(activation_bytes).hexdigest(),
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None, None


def _deployed_subject(runtime_root: Path) -> tuple[dict[str, str] | None, dict[str, Any]]:
    identity_path = runtime_root / "activation" / "runtime-source-identity.json"
    version_path = runtime_root / "VERSION"
    manifest_path = runtime_root / ".aota" / "project.yaml"
    try:
        identity_bytes = _bounded_file(identity_path, MAX_RUNTIME_IDENTITY_BYTES)
        identity = json.loads(identity_bytes)
        version = _bounded_file(version_path, 4096).decode("utf-8").strip()
        manifest = _bounded_file(manifest_path, 256 * 1024).decode("utf-8")
        import yaml

        project_id = yaml.safe_load(manifest)["project"]["id"]
        subject = runtime_subject(
            project_id=project_id,
            source_commit=identity["source_commit"],
            source_tree=identity["source_tree"],
            version=version,
        )
        return subject, {
            "runtime_identity_sha256": hashlib.sha256(identity_bytes).hexdigest(),
            "runtime_archive_sha256": identity.get("source_archive_sha256"),
            "evidence_ref": f"runtime-identity:sha256:{hashlib.sha256(identity_bytes).hexdigest()}",
        }
    except (KeyError, TypeError, ValueError, OSError, UnicodeError, json.JSONDecodeError):
        return None, {}


def _running_subject(
    runtime_root: Path, runtime_id: str, pid: int
) -> tuple[dict[str, str] | None, dict[str, Any]]:
    process = Path("/proc") / str(pid)
    try:
        environment = _parse_process_environment(_bounded_file(process / "environ", 64 * 1024))
        arguments = _bounded_file(process / "cmdline", 16 * 1024).decode("utf-8").split("\0")
        if (
            environment.get("AOTA_FORGE_REPO_ROOT") != str(runtime_root)
            or runtime_root.name != runtime_id
            or "serve" not in arguments
            or "--port" not in arguments
            or "4096" not in arguments
            or not _is_service_main_pid(pid)
        ):
            return None, {}
        subject, refs = _deployed_subject(runtime_root)
        return subject, {
            "pid": pid,
            "process_start_time": _process_start_time(process / "stat"),
            "runtime_id": runtime_id,
            "evidence_ref": f"systemd-mainpid:{pid}:runtime:{runtime_id}",
            "runtime_identity_sha256": refs.get("runtime_identity_sha256"),
        }
    except (OSError, UnicodeError, ValueError):
        return None, {}


def _is_service_main_pid(pid: int) -> bool:
    try:
        main_pid = subprocess.run(
            ["systemctl", "--user", "show", "opencode-af-reference.service", "--property=MainPID", "--value"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return False
    return main_pid.isdigit() and int(main_pid) == pid


def _parse_process_environment(raw: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw.decode("utf-8", "replace").split("\0"):
        key, separator, value = item.partition("=")
        if separator:
            result[key] = value
    return result


def _process_start_time(path: Path) -> str | None:
    try:
        stat_fields = path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        start_ticks = int(stat_fields[19])
        clock_ticks = os.sysconf("SC_CLK_TCK")
        boot = next(
            float(line.split()[1])
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines()
            if line.startswith("btime ")
        )
        return f"{boot + start_ticks / clock_ticks:.3f}"
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def _bounded_file(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"bounded runtime evidence is unavailable: {path.name}")
    return path.read_bytes()
