"""Deterministic, read-only Governance lifecycle readiness inspection.

Doctor is a projection over the existing Plan, Governance, coordinator,
execution, Git and generated-view owners.  It creates no readiness store and
never repairs the state it reports.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    local_plan_authority_reference,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import load_project
from aota_forge.governance.generated_views import (
    render_map_markdown,
    render_status_markdown,
)
from aota_forge.governance.local_lifecycle import preflight_local_plan_candidate
from aota_forge.governance.projection import (
    GovernanceProjectionEngine,
    GovernanceProjectionInput,
    plan_document_input,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectGovernanceStore,
    ProjectPlanRecord,
)
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding

DOCTOR_OPERATION = "governance.doctor"
DOCTOR_READ_ONLY = True
DOCTOR_IS_AUTHORITY = False
DOCTOR_IS_PLANNER = False
DOCTOR_MUTATION_ALLOWED = False
DOCTOR_AUTO_REPAIR = False
DOCTOR_SECOND_GOVERNANCE_ENGINE = False
DOCTOR_SECOND_VALIDATOR_SET = False
DOCTOR_SECOND_PERMISSION_ENGINE = False
DOCTOR_SECOND_OPERATION_REGISTRY = False
MAX_DOCTOR_RESULT_BYTES = 3000
MAX_DOCTOR_FINDINGS = 32

DOCTOR_DOMAINS: tuple[str, ...] = (
    "AUTHORITY",
    "PLAN_LIFECYCLE",
    "COORDINATOR_ROUTING",
    "PROJECTION",
    "SOURCE_FRONTIER",
    "RUNTIME_IDENTITY",
    "SESSION_EXECUTION_CONFLICTS",
    "TRANSITION_READINESS",
)
TRANSITIONS: tuple[str, ...] = (
    "PLAN_INIT",
    "MILESTONE_ENTRY",
    "WORK_EXECUTION",
    "REVIEW_ADOPTION",
    "MILESTONE_CLOSE",
    "PLAN_CLOSE",
    "RUNTIME_ACTIVATION",
    "RECOVERY",
)
_STATUS_RANK = {"READY": 0, "ADVISORY": 1, "DEGRADED": 2, "BLOCKED": 3}


class DoctorStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    DEGRADED = "DEGRADED"
    ADVISORY = "ADVISORY"


@dataclass(frozen=True)
class DoctorFinding:
    domain: str
    status: DoctorStatus
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "domain": self.domain,
            "status": self.status.value,
            "code": self.code,
            "detail": self.detail[:128],
        }


@dataclass(frozen=True)
class DoctorResult:
    overall_status: DoctorStatus
    domains: Mapping[str, str]
    findings: tuple[DoctorFinding, ...]
    transition_readiness: Mapping[str, Mapping[str, str]]
    findings_truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status.value,
            "domains": {domain: self.domains[domain] for domain in DOCTOR_DOMAINS},
            "findings": [finding.to_dict() for finding in self.findings],
            "findings_truncated": self.findings_truncated,
            "transition_readiness": {
                transition: dict(self.transition_readiness[transition])
                for transition in TRANSITIONS
            },
            "doctor_is_authority": False,
            "doctor_is_planner": False,
            "mutation_authorization": "NOT_EVALUATED",
            "durable_materialization": "NOT_ATTEMPTED",
        }


@dataclass(frozen=True)
class DoctorContext:
    """Trusted existing owners and identity supplied by runtime composition."""

    project_id: str
    plan_id: str
    plan_authority_binding: PlanAuthorityBinding | None
    governance_root: LocalGovernanceRootBinding | None
    governance_store: ProjectGovernanceStore | None
    project_root: str | Path | None
    coordinator_store: Any | None = None
    execution_store: Any | None = None
    expected_coordinator_id: str | None = None
    runtime_plan_authority_binding: PlanAuthorityBinding | None = None
    runtime_live_plan_authority: str | None = None
    session_ref: str = ""


def _expected_plan_ref(plan_id: str) -> Any:
    return object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN))


def _is_terminal_body(document: Any) -> bool:
    status = str(getattr(document, "plan_status", "") or "").strip().casefold().replace("_", "-")
    return status == "completed"


def _nonterminal_coordinator(state: Any) -> bool:
    status = getattr(getattr(state, "status", None), "value", getattr(state, "status", None))
    return status in {"ACTIVE", "USER_GATE_REQUIRED", "SESSION_RECOVERY_REQUIRED"}


def _coordinator_work_ids(state: Any) -> set[str]:
    bindings = getattr(state, "bindings", {})
    if not isinstance(bindings, Mapping):
        return set()
    return {
        str(entry.get("canonical_task_id"))
        for entry in bindings.values()
        if isinstance(entry, Mapping) and isinstance(entry.get("canonical_task_id"), str)
    }


def _render_expected_projection(
    *,
    context: DoctorContext,
    records: tuple[ProjectPlanRecord, ...],
    documents: tuple[Any, ...],
    coordinator_states: tuple[Any, ...] | None,
    execution_records: tuple[Any, ...] | None,
) -> tuple[bytes, bytes]:
    assert context.project_root is not None
    project_root = Path(context.project_root)
    if project_root.is_symlink() or project_root.resolve(strict=True) != project_root:
        raise ValueError("trusted Project root is not canonical")
    assert context.governance_root is not None
    context.governance_root.revalidate()
    manifest_path = project_root / ".aota" / "project.yaml"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("trusted Project manifest is unavailable")
    manifest_root, manifest = load_project(manifest_path)
    if manifest_root != project_root or manifest["project"]["id"] != context.project_id:
        raise ValueError("trusted Project identity does not match its manifest")
    source = GovernanceProjectionInput(
        project_id=context.project_id,
        project_ref=f"project:{context.project_id}",
        project_status=manifest["project"].get("status"),
        governance_root_refs=("local-governance",),
        plan_records=records,
        plan_documents=documents,
        coordinator_states=coordinator_states,
        execution_records=execution_records,
    )
    bundle = GovernanceProjectionEngine().rebuild(source)
    return (
        render_map_markdown(bundle).encode("utf-8"),
        render_status_markdown(bundle).encode("utf-8"),
    )


def run_governance_doctor(
    context: DoctorContext,
    *,
    source_observation: Mapping[str, Any] | None,
) -> DoctorResult:
    """Aggregate current truth deterministically without repairing any owner."""
    if not isinstance(context, DoctorContext):
        raise TypeError("context must be a DoctorContext")
    findings: list[DoctorFinding] = []
    finding_keys: set[tuple[str, str, str, str]] = set()

    def add(domain: str, status: DoctorStatus, code: str, detail: str) -> None:
        key = (domain, status.value, code, detail)
        if key in finding_keys or len(findings) >= 64:
            return
        finding_keys.add(key)
        findings.append(DoctorFinding(domain, status, code, detail))

    binding = context.plan_authority_binding
    if not isinstance(binding, PlanAuthorityBinding):
        add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_MISSING", "A trusted current Plan authority binding is required.")
    elif binding.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_MISSING", "Doctor V0 requires the current Local Governance authority adapter.")
    if not is_plan_id(context.plan_id) or (binding is not None and binding.plan_id != context.plan_id):
        add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_BINDING_MISMATCH", "Trusted Plan identity does not match the current binding.")
    if context.governance_root is None or context.governance_store is None:
        add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_MISSING", "Current Governance authority owners are unavailable.")

    records: tuple[ProjectPlanRecord, ...] = ()
    records_available = False
    documents: list[Any] = []
    records_by_id: dict[str, ProjectPlanRecord] = {}
    bodies_by_id: dict[str, str] = {}
    current_record: ProjectPlanRecord | None = None
    coordinator_states: tuple[Any, ...] | None = None
    execution_records: tuple[Any, ...] | None = None

    if context.governance_store is not None:
        try:
            records = tuple(context.governance_store.list_plans(context.project_id))
            records_available = True
            records_by_id = {record.plan_id: record for record in records}
            current_record = records_by_id.get(context.plan_id)
            if current_record is None:
                add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_MISSING", "The current Plan is absent from the Governance Store.")
                add("PLAN_LIFECYCLE", DoctorStatus.BLOCKED, "CURRENT_PLAN_MISSING", "The current Plan lifecycle record is unavailable.")
            elif binding is not None and current_record.authority != binding:
                add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_BINDING_MISMATCH", "The Governance Store binding differs from the trusted runtime binding.")
            if context.runtime_plan_authority_binding is not None and current_record is not None:
                if current_record.authority != context.runtime_plan_authority_binding:
                    add("RUNTIME_IDENTITY", DoctorStatus.BLOCKED, "RUNTIME_BINDING_MISMATCH", "Runtime Plan binding differs from the current Governance Store binding.")
            if (
                context.runtime_live_plan_authority is not None
                and binding is not None
                and context.runtime_live_plan_authority != binding.authority_ref
            ):
                add("RUNTIME_IDENTITY", DoctorStatus.BLOCKED, "RUNTIME_BINDING_MISMATCH", "Runtime live Plan identity differs from the trusted authority binding.")

            if context.governance_root is not None:
                for record in records:
                    if record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
                        continue
                    try:
                        destination = LocalPlanAuthorityDestination(
                            context.governance_root,
                            record.plan_id,
                            _expected_plan_ref(record.plan_id),
                        )
                        snapshot = LocalPlanAuthorityReadAdapter(
                            destination,
                            binding=record.authority,
                        ).load()
                        if (
                            record.authority.source_revision is None
                            or record.authority.source_digest is None
                            or str(snapshot.revision) != str(record.authority.source_revision)
                            or snapshot.digest != record.authority.source_digest
                        ):
                            add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_DRIFT", "Local Plan source revision or digest differs from its trusted Governance binding.")
                            continue
                        bodies_by_id[record.plan_id] = snapshot.body
                        document = normalize_portable_plan(
                            snapshot.body,
                            source_revision=snapshot.revision,
                            source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
                        )
                        documents.append(
                            plan_document_input(
                                plan_id=record.plan_id,
                                document=document,
                                title=getattr(document, "title", None),
                                current_milestone=getattr(document, "current_milestone", None),
                                authority=record.authority,
                            )
                        )
                    except Exception:
                        add("AUTHORITY", DoctorStatus.BLOCKED, "PLAN_AUTHORITY_DRIFT", "A Local Plan source could not be verified against its trusted Governance binding.")
        except Exception:
            add("AUTHORITY", DoctorStatus.BLOCKED, "AUTHORITY_STORE_READ_FAILED", "The current Governance Store could not be read.")

    if current_record is not None:
        current_document = next(
            (item.document for item in documents if item.plan_id == context.plan_id),
            None,
        )
        if current_document is None:
            add("PLAN_LIFECYCLE", DoctorStatus.BLOCKED, "CURRENT_PLAN_UNREADABLE", "The current Plan body could not be verified.")
    for record in records:
        document_input = next((item for item in documents if item.plan_id == record.plan_id), None)
        if document_input is None:
            continue
        document = document_input.document
        if not str(getattr(document, "plan_status", "") or "").strip():
            add("PLAN_LIFECYCLE", DoctorStatus.BLOCKED, "PLAN_LIFECYCLE_STATUS_MISSING", "A current Plan body has no canonical lifecycle status.")
        elif (
            record.lifecycle_state == PLAN_LIFECYCLE_RETIRED
            and not _is_terminal_body(document)
        ):
            add("PLAN_LIFECYCLE", DoctorStatus.BLOCKED, "PLAN_LIFECYCLE_INCONSISTENT", "Plan Store lifecycle and current Plan body make inconsistent terminal claims.")
        elif record.lifecycle_state == PLAN_LIFECYCLE_ACTIVE:
            admission = preflight_local_plan_candidate(
                candidate_kind="init",
                project_id=record.project_id,
                plan_id=record.plan_id,
                candidate_body=bodies_by_id[record.plan_id],
            )
            if "PLAN_STATUS_NOT_INITIALIZABLE" in admission.finding_codes:
                add("PLAN_LIFECYCLE", DoctorStatus.BLOCKED, "PLAN_LIFECYCLE_INCONSISTENT", "An active Plan Store record has a terminal-only Plan body status.")

    if context.coordinator_store is not None:
        try:
            coordinator_states = tuple(
                state
                for state in context.coordinator_store.list_all()
                if getattr(state, "project_id", None) == context.project_id
            )
            by_identity: dict[tuple[str, str, str], set[str]] = defaultdict(set)
            by_coordinator_id: dict[str, tuple[str, str, str]] = {}
            for state in coordinator_states:
                coordinator_id = str(getattr(state, "coordinator_id", ""))
                identity = (
                    str(getattr(state, "project_id", "")),
                    str(getattr(state, "plan_authority", "")),
                    str(getattr(state, "milestone_id", "")),
                )
                if _nonterminal_coordinator(state):
                    by_identity[identity].add(coordinator_id)
                    previous = by_coordinator_id.get(coordinator_id)
                    if previous is not None and previous != identity:
                        add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "COORDINATOR_IDENTITY_COLLISION", "One coordinator identity maps to incompatible Plan or Milestone bindings.")
                    by_coordinator_id[coordinator_id] = identity
                matched = next(
                    (record for record in records if record.authority.authority_ref == identity[1]),
                    None,
                )
                if matched is not None and matched.lifecycle_state == PLAN_LIFECYCLE_RETIRED and _nonterminal_coordinator(state):
                    add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "RETIRED_PLAN_ACTIVE_COORDINATOR", "A nonterminal coordinator remains for a retired Plan.")
                if (
                    binding is not None
                    and identity[1] == binding.authority_ref
                    and _nonterminal_coordinator(state)
                ):
                    current_plan = next(
                        (item.document for item in documents if item.plan_id == binding.plan_id),
                        None,
                    )
                    current_milestone = getattr(current_plan, "current_milestone", None)
                    if current_milestone is not None and identity[2] != current_milestone:
                        add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "LEGACY_COORDINATOR_NOT_ROUTABLE", "Coordinator identity does not use the current Plan-aware routing key.")
                    elif context.expected_coordinator_id is None:
                        add("COORDINATOR_ROUTING", DoctorStatus.DEGRADED, "COORDINATOR_IDENTITY_VALIDATOR_NOT_AVAILABLE", "Trusted runtime did not supply the W1 coordinator identity selected for this Plan and Milestone.")
                    elif coordinator_id != context.expected_coordinator_id:
                        add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "LEGACY_COORDINATOR_NOT_ROUTABLE", "Coordinator identity does not use the current Plan-aware routing key.")
                    if (
                        getattr(state, "plan_digest", None) != binding.source_digest
                        or str(getattr(state, "plan_source_revision", None)) != str(binding.source_revision)
                    ):
                        add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "COORDINATOR_PLAN_BINDING_MISMATCH", "Coordinator source revision or digest differs from the current Plan binding.")
                elif matched is None and _nonterminal_coordinator(state):
                    add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "LEGACY_COORDINATOR_NOT_ROUTABLE", "A nonterminal coordinator has no current Governance Plan binding.")
            if any(len(ids) > 1 for ids in by_identity.values()):
                add("COORDINATOR_ROUTING", DoctorStatus.BLOCKED, "COORDINATOR_IDENTITY_COLLISION", "Multiple nonterminal coordinators share one Plan and Milestone identity.")
        except Exception:
            add("COORDINATOR_ROUTING", DoctorStatus.DEGRADED, "COORDINATOR_READ_FAILED", "The durable coordinator owner could not be read.")
            coordinator_states = None
    else:
        add("COORDINATOR_ROUTING", DoctorStatus.DEGRADED, "COORDINATOR_OWNER_NOT_AVAILABLE", "The durable coordinator owner is not present in this runtime binding.")

    if context.execution_store is not None:
        try:
            execution_records = tuple(context.execution_store.list_all())
        except Exception:
            add("SESSION_EXECUTION_CONFLICTS", DoctorStatus.DEGRADED, "EXECUTION_READ_FAILED", "The durable execution owner could not be read.")
            execution_records = None
    else:
        add("SESSION_EXECUTION_CONFLICTS", DoctorStatus.DEGRADED, "EXECUTION_OWNER_NOT_AVAILABLE", "The durable execution owner is not present in this runtime binding.")

    if execution_records is not None:
        coordinator_refs: dict[str, list[Any]] = defaultdict(list)
        for state in coordinator_states or ():
            for canonical_task_id in _coordinator_work_ids(state):
                coordinator_refs[canonical_task_id].append(state)
        retired_authorities = {
            record.authority.authority_ref
            for record in records
            if record.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        }
        conflict = False
        for record in execution_records:
            if getattr(getattr(record, "canonical_task_state", None), "is_terminal", False):
                continue
            task_id = str(getattr(record, "canonical_task_id", ""))
            origin = getattr(record, "origin_session_ref", None)
            origin_value = str(getattr(origin, "value", origin) or "")
            relevant = task_id.startswith(f"{context.project_id}:") or (
                bool(context.session_ref) and origin_value == context.session_ref
            ) or task_id in coordinator_refs
            if not relevant:
                continue
            refs = coordinator_refs.get(task_id, [])
            if len(refs) != 1 or not _nonterminal_coordinator(refs[0]):
                conflict = True
                break
            if getattr(refs[0], "plan_authority", None) in retired_authorities:
                conflict = True
                break
        if conflict:
            add("SESSION_EXECUTION_CONFLICTS", DoctorStatus.BLOCKED, "NONTERMINAL_EXECUTION_CONFLICT", "Nonterminal execution evidence is orphaned, multiply bound, or associated with a non-routable coordinator.")

    if source_observation is None or source_observation.get("available") is not True:
        add("SOURCE_FRONTIER", DoctorStatus.DEGRADED, "SOURCE_OBSERVATION_UNAVAILABLE", "Trusted Git source observation is unavailable.")
    elif source_observation.get("complete") is not True:
        add("SOURCE_FRONTIER", DoctorStatus.DEGRADED, "SOURCE_OBSERVATION_INCOMPLETE", "Trusted Git source observation is incomplete.")
    elif source_observation.get("is_dirty") is True:
        add("SOURCE_FRONTIER", DoctorStatus.DEGRADED, "SOURCE_RUNTIME_CLASSIFICATION_PENDING", "Dirty source entries cannot yet be separated from legitimate runtime artifacts.")

    if (
        context.plan_authority_binding is None
        or context.governance_root is None
        or context.governance_store is None
        or context.project_root is None
    ):
        add("RUNTIME_IDENTITY", DoctorStatus.BLOCKED, "RUNTIME_CONTEXT_INCOMPLETE", "Trusted Project, Plan, and Local Governance runtime bindings are required.")
    add("RUNTIME_IDENTITY", DoctorStatus.ADVISORY, "RUNTIME_ACTIVE_ELIGIBILITY_NOT_YET_PROVEN", "Explicit active deployment eligibility remains an M2 validator.")

    if (
        not records_available
        or context.governance_root is None
        or context.project_root is None
        or context.governance_store is None
    ):
        add("PROJECTION", DoctorStatus.DEGRADED, "PROJECTION_READ_FAILED", "Trusted projection inputs are incomplete.")
    else:
        try:
            expected_map, expected_status = _render_expected_projection(
                context=context,
                records=records,
                documents=tuple(documents),
                coordinator_states=coordinator_states,
                execution_records=execution_records,
            )
            root = Path(context.governance_root.root_path)
            map_path = root / "MAP.md"
            status_path = root / "STATUS.md"
            if map_path.is_symlink() or status_path.is_symlink():
                raise OSError("generated projection destination is not a regular file")
            if not map_path.is_file() or not status_path.is_file():
                add("PROJECTION", DoctorStatus.BLOCKED, "PROJECTION_MISSING", "One or more generated Governance views are missing.")
            elif map_path.read_bytes() != expected_map or status_path.read_bytes() != expected_status:
                add("PROJECTION", DoctorStatus.BLOCKED, "PROJECTION_STALE", "Generated MAP and STATUS differ from the deterministic in-memory projection.")
        except Exception:
            add("PROJECTION", DoctorStatus.DEGRADED, "PROJECTION_READ_FAILED", "Generated views or trusted projection inputs could not be read.")

    add(
        "TRANSITION_READINESS",
        DoctorStatus.ADVISORY,
        "VALIDATOR_NOT_YET_AVAILABLE",
        "Canonical deterministic validators are not yet available for all reported transitions.",
    )

    domain_status: dict[str, str] = {domain: DoctorStatus.READY.value for domain in DOCTOR_DOMAINS}
    for finding in findings:
        current = domain_status[finding.domain]
        if _STATUS_RANK[finding.status.value] > _STATUS_RANK[current]:
            domain_status[finding.domain] = finding.status.value
    ordered_findings = tuple(
        sorted(
            findings,
            key=lambda finding: (
                DOCTOR_DOMAINS.index(finding.domain),
                finding.code,
                finding.detail,
            ),
        )
    )
    if any(finding.status is DoctorStatus.BLOCKED for finding in ordered_findings):
        overall = DoctorStatus.BLOCKED
    elif any(finding.status is DoctorStatus.DEGRADED for finding in ordered_findings):
        overall = DoctorStatus.DEGRADED
    else:
        overall = DoctorStatus.READY
    transition_readiness = {
        transition: {
            "status": DoctorStatus.ADVISORY.value,
            "code": "VALIDATOR_NOT_YET_AVAILABLE",
        }
        for transition in TRANSITIONS
    }
    visible_findings = list(ordered_findings[:MAX_DOCTOR_FINDINGS])
    truncated = len(visible_findings) < len(ordered_findings)
    result = DoctorResult(
        overall,
        domain_status,
        tuple(visible_findings),
        transition_readiness,
        findings_truncated=truncated,
    )
    while (
        len(
            json.dumps(
                result.to_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        )
        > MAX_DOCTOR_RESULT_BYTES
        and visible_findings
    ):
        visible_findings.pop()
        result = DoctorResult(
            overall,
            domain_status,
            tuple(visible_findings),
            transition_readiness,
            findings_truncated=True,
        )
    return result


__all__ = [
    "DOCTOR_DOMAINS",
    "DOCTOR_IS_AUTHORITY",
    "DOCTOR_IS_PLANNER",
    "DOCTOR_READ_ONLY",
    "DOCTOR_AUTO_REPAIR",
    "DOCTOR_MUTATION_ALLOWED",
    "DOCTOR_OPERATION",
    "MAX_DOCTOR_FINDINGS",
    "MAX_DOCTOR_RESULT_BYTES",
    "DOCTOR_SECOND_GOVERNANCE_ENGINE",
    "DOCTOR_SECOND_OPERATION_REGISTRY",
    "DOCTOR_SECOND_PERMISSION_ENGINE",
    "DOCTOR_SECOND_VALIDATOR_SET",
    "DoctorContext",
    "DoctorFinding",
    "DoctorResult",
    "DoctorStatus",
    "TRANSITIONS",
    "run_governance_doctor",
]
