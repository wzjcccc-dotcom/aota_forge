"""Bounded recovery of a missing local ProjectPlanRecord.

This seam adopts metadata for an already-existing local Plan authority.  It is
deliberately not Plan initialization: it never writes the authority document,
does not create a journal lineage, and only uses the existing Governance Store
create primitive after all trusted identity and source checks pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
)
from aota_forge.composition.project_binding import TrustedProjectBindingEvidence
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.core.plan.sections import parse_body_sections
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE, load_project
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PlanRecordAlreadyExistsError,
    ProjectGovernanceStore,
    ProjectPlanRecord,
)
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


REHYDRATION_IS_PLAN_INIT = False
PLAN_AUTHORITY_MUTATED = False
SECOND_GOVERNANCE_STORE = False
SECOND_PLAN_MUTATION_PROTOCOL = False
RAW_SQL_INSERT_USED = False
DIRECT_SQLITE_MUTATION_USED = False
RAW_PUT_PLAN_OPERATOR_REPAIR = False
FAKE_HISTORICAL_RECORD_REVISION = False
FAKE_PLAN_INIT_JOURNAL_EVENT = False
NEW_PUBLIC_OPERATION = False


class PlanRecordRehydrationError(ValueError):
    """Fail-closed error from the bounded metadata rehydration seam."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _error(code: str, message: str) -> PlanRecordRehydrationError:
    return PlanRecordRehydrationError(code, message)


def _require_project_identity(
    project_id: str,
    project_binding: TrustedProjectBindingEvidence,
) -> None:
    if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
        raise _error("PROJECT_IDENTITY_INVALID", "project_id must be canonical")
    if not isinstance(project_binding, TrustedProjectBindingEvidence):
        raise _error("PROJECT_IDENTITY_INVALID", "trusted Project resolver evidence is required")
    resolution = project_binding.resolution
    if resolution.status != "RESOLVED" or len(resolution.candidates) != 1:
        code = "PROJECT_IDENTITY_AMBIGUOUS" if len(resolution.candidates) > 1 else "PROJECT_IDENTITY_INVALID"
        raise _error(code, "trusted Project resolution is not singular")
    if project_binding.project_id != project_id:
        raise _error("PROJECT_IDENTITY_MISMATCH", "trusted Project identity differs from project_id")

    candidate = resolution.candidates[0]
    if candidate.project_id != project_id:
        raise _error("PROJECT_IDENTITY_MISMATCH", "resolved Project candidate differs from project_id")
    project_root = Path(candidate.project_root)
    manifest = Path(candidate.manifest_path)
    if not manifest.is_absolute():
        manifest = Path(resolution.workspace_root) / manifest
    if project_root.is_symlink() or manifest.is_symlink():
        raise _error("PROJECT_IDENTITY_INVALID", "trusted Project identity contains a symlink")
    try:
        canonical_root = project_root.resolve(strict=True)
        canonical_manifest = manifest.resolve(strict=True)
    except OSError as exc:
        raise _error("PROJECT_IDENTITY_INVALID", f"trusted Project identity cannot be resolved: {exc}") from exc
    if canonical_root != project_root or canonical_manifest != manifest:
        raise _error("PROJECT_IDENTITY_INVALID", "trusted Project identity is not canonical")
    if manifest != project_root / ".aota" / "project.yaml" or not manifest.is_file():
        raise _error("PROJECT_IDENTITY_INVALID", "trusted source project manifest is missing")
    try:
        loaded_root, manifest_data = load_project(manifest)
    except Exception as exc:
        raise _error("PROJECT_IDENTITY_INVALID", f"trusted source project manifest is invalid: {exc}") from exc
    if loaded_root != project_root or manifest_data["project"]["id"] != project_id:
        raise _error("PROJECT_IDENTITY_MISMATCH", "source project manifest does not prove project_id")


def _destination(
    governance_root: LocalGovernanceRootBinding,
    plan_id: str,
) -> LocalPlanAuthorityDestination:
    expected_ref = object_ref_subject(
        make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN)
    )
    return LocalPlanAuthorityDestination(governance_root, plan_id, expected_ref)


def _plan_identity(body: str) -> dict[str, str]:
    sections = parse_body_sections(body)
    identity_sections = [section for section in sections if section.title.strip() == "Plan Identity"]
    if len(identity_sections) != 1:
        raise _error(
            "PLAN_IDENTITY_INVALID",
            "authoritative Plan must contain exactly one Plan Identity section",
        )
    return identity_sections[0].key_values


def _validate_authority_identity(
    supplied: PlanAuthorityBinding | None,
    derived: PlanAuthorityBinding,
) -> None:
    if supplied is None:
        return
    if not isinstance(supplied, PlanAuthorityBinding):
        raise _error("AUTHORITY_BINDING_INVALID", "authority must be a PlanAuthorityBinding")
    if (
        supplied.plan_id != derived.plan_id
        or supplied.source_kind != derived.source_kind
        or supplied.authority_ref != derived.authority_ref
    ):
        raise _error(
            "AUTHORITY_BINDING_MISMATCH",
            "supplied authority identity does not match the trusted local destination",
        )


def _validate_observed_binding(
    supplied: PlanAuthorityBinding | None,
    observed: PlanAuthorityBinding,
) -> None:
    if supplied is None:
        return
    if supplied.source_revision is not None and supplied.source_revision != observed.source_revision:
        raise _error("AUTHORITY_BINDING_STALE", "supplied source revision is stale")
    if supplied.source_digest is not None and supplied.source_digest != observed.source_digest:
        raise _error("AUTHORITY_BINDING_STALE", "supplied source digest is stale")


def _exact_record_or_conflict(
    current: ProjectPlanRecord | None,
    expected: ProjectPlanRecord,
) -> ProjectPlanRecord | None:
    if current is None:
        return None
    if current == expected:
        return current
    raise _error(
        "CONFLICTING_PROJECT_PLAN_RECORD",
        "existing ProjectPlanRecord differs from the trusted local authority facts",
    )


def _readback(
    store: ProjectGovernanceStore,
    expected: ProjectPlanRecord,
) -> ProjectPlanRecord:
    readback = store.get_plan(expected.project_id, expected.plan_id)
    if readback != expected:
        raise _error(
            "PROJECT_PLAN_RECORD_READBACK_FAILED",
            "ProjectPlanRecord did not verify exactly after rehydration",
        )
    return readback


def rehydrate_existing_local_plan_record(
    *,
    project_id: str,
    plan_id: str,
    project_binding: TrustedProjectBindingEvidence,
    governance_root: LocalGovernanceRootBinding,
    governance_store: ProjectGovernanceStore,
    authority_binding: PlanAuthorityBinding | None = None,
) -> ProjectPlanRecord:
    """Adopt an existing local Plan into the already-resolved Governance Store.

    ``project_binding``, ``governance_root`` and ``governance_store`` are
    trusted composition outputs.  The function intentionally accepts no path,
    lifecycle state, source digest, or record revision from semantic callers.
    """
    if not is_plan_id(plan_id):
        raise _error("PLAN_ID_INVALID", "plan_id must be canonical")
    if not isinstance(governance_root, LocalGovernanceRootBinding):
        raise _error("LOCAL_AUTHORITY_INVALID", "trusted LocalGovernanceRootBinding is required")
    if governance_root.project_id != project_id:
        raise _error("LOCAL_AUTHORITY_INVALID", "local governance root differs from project_id")
    if not isinstance(governance_store, ProjectGovernanceStore):
        raise _error("GOVERNANCE_STORE_INVALID", "resolved ProjectGovernanceStore is required")
    _require_project_identity(project_id, project_binding)

    try:
        governance_root.revalidate()
        destination = _destination(governance_root, plan_id)
        derived_binding = PlanAuthorityBinding(
            plan_id=plan_id,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=destination.authority_ref,
        )
        _validate_authority_identity(authority_binding, derived_binding)
        reader = LocalPlanAuthorityReadAdapter(destination, binding=derived_binding)
        snapshot = reader.load()
    except PlanRecordRehydrationError:
        raise
    except Exception as exc:
        raise _error("LOCAL_AUTHORITY_READ_FAILED", str(exc)) from exc

    if not snapshot.body:
        raise _error("AUTHORITY_DOCUMENT_MISSING", "existing local Plan authority is empty or missing")
    try:
        raw_digest = hashlib.sha256(snapshot.body.encode("utf-8")).hexdigest()
    except (UnicodeEncodeError, AttributeError) as exc:
        raise _error("AUTHORITY_DOCUMENT_INVALID", "local Plan authority is not valid UTF-8 text") from exc
    if snapshot.digest != raw_digest:
        raise _error("SOURCE_DIGEST_MISMATCH", "local Plan adapter digest does not match authority bytes")
    if snapshot.revision is None or isinstance(snapshot.revision, bool):
        raise _error("SOURCE_REVISION_INVALID", "local Plan authority has no valid observed revision")
    if not isinstance(snapshot.revision, (str, int)):
        raise _error("SOURCE_REVISION_INVALID", "local Plan authority revision must be str|int")
    if isinstance(snapshot.revision, str) and not snapshot.revision:
        raise _error("SOURCE_REVISION_INVALID", "local Plan authority revision must be non-empty")
    if isinstance(snapshot.revision, int) and snapshot.revision < 0:
        raise _error("SOURCE_REVISION_INVALID", "local Plan authority revision must be non-negative")

    observed_binding = PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=destination.authority_ref,
        source_revision=snapshot.revision,
        source_digest=raw_digest,
    )
    _validate_observed_binding(authority_binding, observed_binding)

    try:
        document = normalize_portable_plan(
            snapshot.body,
            source_revision=str(snapshot.revision),
            source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
        )
    except Exception as exc:
        raise _error("PLAN_AUTHORITY_INVALID", f"authoritative Plan normalization failed: {exc}") from exc

    try:
        identity = _plan_identity(snapshot.body)
    except PlanRecordRehydrationError:
        raise
    except Exception as exc:
        raise _error("PLAN_IDENTITY_INVALID", f"authoritative Plan identity could not be read: {exc}") from exc
    if identity.get("PROJECT_ID") != project_id:
        raise _error("PLAN_PROJECT_ID_MISMATCH", "Plan Identity PROJECT_ID differs from trusted project_id")
    if identity.get("PLAN_ID") != plan_id:
        raise _error("PLAN_ID_MISMATCH", "Plan Identity PLAN_ID differs from requested plan_id")
    if identity.get("PLAN_STATUS") != document.plan_status:
        raise _error("PLAN_STATUS_CONTRADICTION", "Plan Identity and normalized Plan status disagree")
    if document.plan_status != PLAN_LIFECYCLE_ACTIVE:
        raise _error(
            "PLAN_LIFECYCLE_NOT_REHYDRATABLE_AS_ACTIVE",
            "only an authoritative active Plan can be rehydrated",
        )

    expected = ProjectPlanRecord(
        project_id=project_id,
        plan_id=plan_id,
        lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
        authority=observed_binding,
    )
    current = _exact_record_or_conflict(
        governance_store.get_plan(project_id, plan_id),
        expected,
    )
    if current is not None:
        return _readback(governance_store, expected)

    try:
        verification_snapshot = reader.load()
        verification_digest = hashlib.sha256(
            verification_snapshot.body.encode("utf-8")
        ).hexdigest()
    except Exception as exc:
        raise _error("LOCAL_AUTHORITY_READ_FAILED", str(exc)) from exc
    if (
        verification_snapshot.body != snapshot.body
        or verification_snapshot.digest != verification_digest
        or verification_snapshot.revision != snapshot.revision
        or verification_digest != raw_digest
    ):
        raise _error(
            "PLAN_AUTHORITY_CHANGED_DURING_REHYDRATION",
            "local Plan authority changed before metadata could be adopted",
        )

    try:
        governance_store.put_plan(expected)
    except PlanRecordAlreadyExistsError:
        current = _exact_record_or_conflict(
            governance_store.get_plan(project_id, plan_id),
            expected,
        )
        if current is None:
            raise _error(
                "PROJECT_PLAN_RECORD_WRITE_UNKNOWN",
                "record appeared during rehydration but could not be read back",
            )
    return _readback(governance_store, expected)


__all__ = [
    "DIRECT_SQLITE_MUTATION_USED",
    "FAKE_HISTORICAL_RECORD_REVISION",
    "FAKE_PLAN_INIT_JOURNAL_EVENT",
    "NEW_PUBLIC_OPERATION",
    "PLAN_AUTHORITY_MUTATED",
    "PlanRecordRehydrationError",
    "RAW_PUT_PLAN_OPERATOR_REPAIR",
    "RAW_SQL_INSERT_USED",
    "REHYDRATION_IS_PLAN_INIT",
    "SECOND_GOVERNANCE_STORE",
    "SECOND_PLAN_MUTATION_PROTOCOL",
    "rehydrate_existing_local_plan_record",
]
