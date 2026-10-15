"""Governance 2.0 Project/Plan intake composition.

This module is deliberately a thin lifecycle service.  Project registration,
local Plan materialization, durable journal/CAS, and generated projections stay
owned by their existing modules; intake only composes them for the two
Agent-facing operation families.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import fcntl
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
)
from aota_forge.core.context import TrustedContext
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.lifecycle import (
    ProjectReconciliationEvidence,
    ProjectRegistrationEvidence,
    reconcile_project,
    register_project,
)
from aota_forge.core.project.manifest import PROJECT_ID_RE, load_project
from aota_forge.governance.context_route import ContextRouteInput, build_context_route
from aota_forge.governance.local_lifecycle import (
    LocalPlanCandidatePreflight,
    LocalPlanLifecycleCoordinator,
    LocalPlanLifecycleError,
    LocalPlanLifecycleRequest,
    PlanCandidateKind,
    PlanCloseMaterializationEvidence,
    preflight_local_plan_candidate,
)
from aota_forge.governance.projection import (
    GovernanceProjectionInput,
    PlanDocumentInput,
    REFRESH_TRIGGER_PLAN_MUTATION,
    plan_document_input,
)
from aota_forge.governance.projection_lifecycle import GovernanceProjectionLifecycle
from aota_forge.governance.project_store import ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.governance.plan_semantics import (
    MAX_PLAN_SECTION_READ_BYTES,
    PLAN_AMENDMENT_CONTRACTS,
    PlanSemanticError,
    PlanSemanticIndex,
    apply_plan_amendment,
    canonical_plan_amendment_digest,
    normalize_plan_amendment,
)
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


GOVERNANCE_PROJECT_OPERATION = "governance.project"
GOVERNANCE_PLAN_OPERATION = "governance.plan"
GOVERNANCE_PLAN_PREFLIGHT_OPERATION = "governance.plan.preflight"
GOVERNANCE_OPERATIONS = (GOVERNANCE_PROJECT_OPERATION, GOVERNANCE_PLAN_OPERATION)
GOVERNANCE_PROJECT_MODES = frozenset({"init", "inspect", "reconcile"})
GOVERNANCE_PLAN_MODES = frozenset({"init", "inspect", "amend", "close"})
GOVERNANCE_PLAN_INSPECT_VIEWS = frozenset({"card", "index", "section", "full"})
DEFAULT_PLAN_INSPECT_VIEW = "card"
STRUCTURED_PLAN_AMENDMENT_KINDS = frozenset(
    contract["fields"]["kind"]["const"] for contract in PLAN_AMENDMENT_CONTRACTS
)

PROJECT_INTAKE_SINGLE_PATH = True
PLAN_INTAKE_SINGLE_PATH = True
PROJECT_INIT_REUSES_PROJECT_REGISTER = True
PROJECT_INIT_REUSES_PROJECT_RECONCILE = True
PLAN_INIT_REUSES_LOCAL_LIFECYCLE = True
PLAN_INIT_TO_INITIAL_PROJECTION_WIRING = True
MAP_MATERIALIZED_ON_INITIAL_INTAKE = True
STATUS_MATERIALIZED_ON_INITIAL_INTAKE = True
PROJECT_CARD_DERIVABLE = True
PLAN_CARD_DERIVABLE = True
MILESTONE_CARD_DERIVABLE = True
CONTEXT_ROUTE_DERIVABLE = True
SECOND_LIFECYCLE_IMPLEMENTATION = False
SECOND_GOVERNANCE_INTAKE_PATH = False

_SAFE_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-")


class GovernanceIntakeError(ValueError):
    """Typed fail-closed Project/Plan intake error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceIntakeError("INPUT_INVALID", f"{label} must be a non-empty string")
    candidate = value.strip()
    if len(candidate) > 256 or any(char not in _SAFE_ID_CHARS for char in candidate):
        raise GovernanceIntakeError("INPUT_INVALID", f"{label} must be a bounded safe identifier")
    return candidate


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _trusted_dir(value: object, label: str, *, required: bool = True) -> Path | None:
    if value is None and not required:
        return None
    if not isinstance(value, (str, os.PathLike)):
        raise GovernanceIntakeError("TRUSTED_CONTEXT_INVALID", f"{label} must be a trusted directory")
    path = Path(value)
    if path.is_symlink() or not path.exists() or not path.is_dir():
        raise GovernanceIntakeError("TRUSTED_CONTEXT_INVALID", f"{label} must be an existing directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise GovernanceIntakeError("TRUSTED_CONTEXT_INVALID", f"{label} cannot be resolved: {exc}") from exc
    if resolved != path:
        raise GovernanceIntakeError("TRUSTED_CONTEXT_INVALID", f"{label} must already be canonical")
    return resolved


@dataclass(frozen=True)
class GovernanceIntakeContext:
    """Trusted operator/runtime inputs for one intake composition.

    None of these fields are accepted from operation arguments.  The model
    supplies only semantic Project/Plan values; the runtime supplies roots,
    registry, and durable storage locations.
    """

    workspace_id: str
    governance_base: str | os.PathLike[str]
    registry_path: str | os.PathLike[str] | None = None
    workspace_root: str | os.PathLike[str] | None = None
    project_root: str | os.PathLike[str] | None = None
    governance_store_path: str | os.PathLike[str] | None = None
    journal_path: str | os.PathLike[str] | None = None
    source_repository: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace_id", _safe_id(self.workspace_id, "workspace_id"))
        governance_base = _trusted_dir(self.governance_base, "governance_base")
        workspace_root = _trusted_dir(self.workspace_root, "workspace_root", required=False)
        project_root = _trusted_dir(self.project_root, "project_root", required=False)
        if self.registry_path is not None:
            registry = Path(self.registry_path)
            if registry.is_symlink() or not registry.is_file():
                raise GovernanceIntakeError(
                    "TRUSTED_CONTEXT_INVALID", "registry_path must be an existing regular file"
                )
            object.__setattr__(self, "registry_path", str(registry.resolve(strict=True)))
        if self.source_repository and (not isinstance(self.source_repository, str) or len(self.source_repository) > 512):
            raise GovernanceIntakeError("TRUSTED_CONTEXT_INVALID", "source_repository is not bounded")
        object.__setattr__(self, "governance_base", str(governance_base))
        object.__setattr__(self, "workspace_root", str(workspace_root) if workspace_root else None)
        object.__setattr__(self, "project_root", str(project_root) if project_root else None)
        for label in ("governance_store_path", "journal_path"):
            raw = getattr(self, label)
            if raw is not None:
                path = Path(raw)
                if path.is_symlink():
                    raise GovernanceIntakeError("TRUSTED_CONTEXT_INVALID", f"{label} must not be a symlink")
                object.__setattr__(self, label, str(path))


def _target_ref(plan_id: str):
    return object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN))


def _public_project_evidence(
    evidence: ProjectRegistrationEvidence | ProjectReconciliationEvidence,
) -> dict[str, Any]:
    payload = evidence.to_dict()
    return {
        key: payload[key]
        for key in (
            "status",
            "workspace_id",
            "project_id",
            "registry_fingerprint",
            "candidate_fingerprint",
            "registry_updated",
            "source_repository",
            "repository_identity",
        )
        if key in payload
    }


def _atomic_write_manifest(path: Path, data: dict[str, Any]) -> None:
    if path.parent.is_symlink() or (path.parent.exists() and not path.parent.is_dir()):
        raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", "project manifest directory is unsafe")
    path.parent.mkdir(mode=0o755, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", "project manifest destination is unsafe")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".project-", suffix=".yaml", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = ""
    except OSError as exc:
        raise GovernanceIntakeError("PROJECT_MANIFEST_WRITE_FAILED", str(exc)) from exc
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


@contextmanager
def _governance_intake_lifecycle_lock(
    context: GovernanceIntakeContext, project_id: str
):
    """Serialize one Project's Plan mutation plus derived projection refresh."""
    governance_base = _trusted_dir(context.governance_base, "governance_base")
    assert governance_base is not None
    lock_suffix = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
    lock_path = governance_base / f".governance-intake-{lock_suffix}.lock"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("Governance intake lock is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise GovernanceIntakeError(
            "GOVERNANCE_LIFECYCLE_LOCK_UNAVAILABLE",
            "Project governance lifecycle lock could not be acquired",
        ) from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _minimal_manifest(
    project_id: str,
    *,
    name: str | None,
    kind: str | None,
    summary: str | None,
    capabilities: list[str] | None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project": {
            "id": project_id,
            "name": name or project_id,
            "kind": kind or "project",
            "status": "active",
        },
        "summary": summary or f"Project {project_id}",
        "capabilities": list(capabilities or []),
        "paths": {
            "source_root": ".",
            "source": [],
            "docs": [],
            "scripts": [],
            "profiles": [],
            "skills": [],
            "tests": [],
        },
        "commands": {"validate": [], "deploy": [], "verify_deploy": []},
        "runtime": {"deployment_type": "manual", "requires_human_checkpoint": True},
        "codegraph": {"enabled": False, "index_location": ".codegraph"},
        "plan": {"active_plan_id": None},
        "constraints": [],
    }


class GovernanceIntakeService:
    """Compose the existing durable Project/Plan owners for one context."""

    def __init__(
        self,
        context: GovernanceIntakeContext,
        *,
        principal: str = "operator",
        decision_source: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(context, GovernanceIntakeContext):
            raise TypeError("context must be GovernanceIntakeContext")
        self.context = context
        self.principal = _safe_id(principal, "principal")
        source = dict(decision_source or {})
        self.decision_source = {
            "owner_type": _safe_id(source.get("owner_type", "trusted_principal"), "decision_owner_type"),
            "owner_id": _safe_id(source.get("owner_id", self.principal), "decision_owner_id"),
            "channel": _safe_id(source.get("channel", "governance_intake"), "decision_channel"),
            "provenance": _safe_id(source.get("provenance", "trusted_service"), "decision_provenance"),
        }
        if isinstance(source.get("freshness"), str) and source["freshness"]:
            self.decision_source["freshness"] = _safe_id(source["freshness"], "decision_freshness")
        self._resolved_project_root: str | None = None

    def project(self, mode: str, *, project_id: str, **arguments: Any) -> dict[str, Any]:
        if mode == "init":
            self._project_id(project_id)
            with _governance_intake_lifecycle_lock(self.context, project_id):
                return self._project_unlocked(
                    mode, project_id=project_id, **arguments
                )
        return self._project_unlocked(mode, project_id=project_id, **arguments)

    def preflight_plan_candidate(
        self,
        *,
        candidate_kind: PlanCandidateKind | str,
        project_id: str,
        plan_id: str,
        candidate_body: str,
    ) -> LocalPlanCandidatePreflight:
        """Read-only readiness check; does not inspect or mutate durable owners."""
        project_id = self._project_id(project_id)
        if not is_plan_id(plan_id):
            raise GovernanceIntakeError(
                "PLAN_ID_INVALID", "plan_id must be one canonical internal Plan ID"
            )
        try:
            return preflight_local_plan_candidate(
                candidate_kind=candidate_kind,
                project_id=project_id,
                plan_id=plan_id,
                candidate_body=candidate_body,
            )
        except LocalPlanLifecycleError as exc:
            raise GovernanceIntakeError(exc.code, exc.message) from exc

    def _project_unlocked(
        self, mode: str, *, project_id: str, **arguments: Any
    ) -> dict[str, Any]:
        if mode not in GOVERNANCE_PROJECT_MODES:
            raise GovernanceIntakeError("MODE_INVALID", f"unsupported governance.project mode: {mode!r}")
        project_id = self._project_id(project_id)
        source_repository = self._source_repository(arguments.get("source_repository"))
        if mode == "init":
            self._prepare_manifest(project_id, arguments)
            registration = self._register_and_reconcile(project_id, source_repository)
            self._ensure_governance_root(project_id)
            return self._project_result(
                mode,
                project_id,
                source_repository,
                registration,
                write_projection=True,
            )
        registration = self._reconcile_registered(project_id, source_repository)
        if mode == "reconcile":
            self._ensure_governance_root(project_id)
        return self._project_result(
            mode,
            project_id,
            source_repository,
            registration,
            write_projection=False,
        )

    def plan(
        self, mode: str, *, project_id: str, plan_id: str, **arguments: Any
    ) -> dict[str, Any]:
        if mode in {"init", "amend", "close"}:
            self._project_id(project_id)
            if not is_plan_id(plan_id):
                raise GovernanceIntakeError(
                    "PLAN_ID_INVALID",
                    "plan_id must be one canonical internal Plan ID",
                )
            with _governance_intake_lifecycle_lock(self.context, project_id):
                return self._plan_unlocked(
                    mode,
                    project_id=project_id,
                    plan_id=plan_id,
                    **arguments,
                )
        return self._plan_unlocked(
            mode,
            project_id=project_id,
            plan_id=plan_id,
            **arguments,
        )

    def _plan_unlocked(
        self, mode: str, *, project_id: str, plan_id: str, **arguments: Any
    ) -> dict[str, Any]:
        if mode not in GOVERNANCE_PLAN_MODES:
            raise GovernanceIntakeError("MODE_INVALID", f"unsupported governance.plan mode: {mode!r}")
        project_id = self._project_id(project_id)
        if not is_plan_id(plan_id):
            raise GovernanceIntakeError("PLAN_ID_INVALID", "plan_id must be one canonical internal Plan ID")
        if mode == "init":
            body = arguments.get("candidate_body")
            if not isinstance(body, str) or not body.strip():
                raise GovernanceIntakeError("PLAN_BODY_REQUIRED", "Plan mutation requires candidate_body")
            try:
                admission = preflight_local_plan_candidate(
                    candidate_kind=mode,
                    project_id=project_id,
                    plan_id=plan_id,
                    candidate_body=body,
                )
            except LocalPlanLifecycleError as exc:
                raise GovernanceIntakeError(exc.code, exc.message) from exc
            if admission.semantic_readiness != "READY":
                codes = ", ".join(admission.finding_codes)
                code = admission.finding_codes[0] if admission.finding_codes else "PLAN_CANDIDATE_NOT_READY"
                raise GovernanceIntakeError(code, f"Plan candidate failed semantic admission: {codes}")
        if mode == "amend":
            if arguments.get("candidate_body") is not None:
                raise GovernanceIntakeError(
                    "WHOLE_BODY_AMENDMENT_NOT_SUPPORTED",
                    "Plan amend accepts a structured semantic amendment, not candidate_body",
                )
            try:
                normalized_amendment = normalize_plan_amendment(arguments.get("amendment"))
            except PlanSemanticError as exc:
                raise GovernanceIntakeError(exc.code, exc.message) from exc
            self._validate_amendment_scope(project_id, plan_id, normalized_amendment)
            expected = arguments.get("expected_plan_revision")
            if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
                raise GovernanceIntakeError(
                    "PLAN_REVISION_REQUIRED", "Plan amendment requires a positive expected_plan_revision"
                )
        if mode == "inspect":
            view = arguments.get("view", DEFAULT_PLAN_INSPECT_VIEW)
            if not isinstance(view, str) or view not in GOVERNANCE_PLAN_INSPECT_VIEWS:
                raise GovernanceIntakeError(
                    "PLAN_VIEW_INVALID", f"view must be one of {sorted(GOVERNANCE_PLAN_INSPECT_VIEWS)}"
                )
            section_ref = arguments.get("section_ref")
            if view == "section" and (not isinstance(section_ref, str) or not section_ref.strip()):
                raise GovernanceIntakeError("PLAN_SECTION_REF_REQUIRED", "section view requires section_ref")
            if view != "section" and section_ref is not None:
                raise GovernanceIntakeError("PLAN_SECTION_REF_UNEXPECTED", "section_ref is only valid with view=section")
        source_repository = self._source_repository(arguments.get("source_repository"))
        registration = (
            self._reconcile_registered(project_id, source_repository)
            if mode == "inspect"
            else self._register_and_reconcile(project_id, source_repository)
        )
        governance_root = self._ensure_governance_root(project_id, create=mode != "inspect")
        store, journal = self._open_owners(project_id)
        try:
            if mode == "init":
                body = arguments.get("candidate_body")
                if not isinstance(body, str) or not body.strip():
                    raise GovernanceIntakeError("PLAN_BODY_REQUIRED", "plan init requires candidate_body")
                current = store.get_plan(project_id, plan_id)
                lifecycle_request = self._lifecycle_request(
                    mode=mode,
                    project_id=project_id,
                    plan_id=plan_id,
                    current=current,
                    candidate_body=body,
                    arguments=arguments,
                )
                try:
                    result = LocalPlanLifecycleCoordinator(store, journal).initialize(
                        lifecycle_request, body
                    )
                except LocalPlanLifecycleError as exc:
                    raise GovernanceIntakeError(exc.code, exc.message) from exc
                if not result.ok:
                    raise GovernanceIntakeError(result.error_code or "PLAN_INIT_FAILED", result.error_message or "Plan init failed")
                return self._plan_result(
                    mode,
                    project_id,
                    plan_id,
                    registration,
                    governance_root,
                    store,
                    source_repository=source_repository,
                    write_projection=True,
                )
            if mode == "amend":
                current = store.get_plan(project_id, plan_id)
                if current is None:
                    raise GovernanceIntakeError("PLAN_RECORD_REQUIRED", "Plan amendment requires an existing Plan record")
                expected_revision = arguments.get("expected_plan_revision")
                if expected_revision != current.revision:
                    raise GovernanceIntakeError("STALE_PLAN_REVISION", "amendment expected_plan_revision is stale")
                snapshot, document = self._read_bound_plan(governance_root, current)
                try:
                    amendment = normalize_plan_amendment(arguments.get("amendment"))
                except PlanSemanticError as exc:
                    raise GovernanceIntakeError(exc.code, exc.message) from exc
                self._validate_amendment_scope(project_id, plan_id, amendment)
                semantic_index = PlanSemanticIndex.build(
                    project_id=project_id,
                    plan_id=plan_id,
                    plan_revision=current.revision,
                    authority_revision=snapshot.revision,
                    source_digest=snapshot.digest or "",
                    body=snapshot.body,
                    document=document,
                )
                target_ref = arguments.get("target_ref")
                expected_section_digest = arguments.get("expected_section_digest")
                if (target_ref is None) != (expected_section_digest is None):
                    raise GovernanceIntakeError(
                        "SECTION_PRECONDITION_INVALID",
                        "target_ref and expected_section_digest must be supplied together",
                    )
                if target_ref is not None:
                    if target_ref != amendment["milestone_id"]:
                        raise GovernanceIntakeError(
                            "SECTION_PRECONDITION_INVALID",
                            "Plan amendment section precondition must target its milestone semantic ref",
                        )
                    try:
                        target_section = semantic_index.resolve(target_ref)
                    except PlanSemanticError as exc:
                        raise GovernanceIntakeError(exc.code, exc.message) from exc
                    if (
                        not isinstance(expected_section_digest, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected_section_digest)
                    ):
                        raise GovernanceIntakeError(
                            "SECTION_PRECONDITION_INVALID", "expected_section_digest must be a SHA-256 digest"
                        )
                    if target_section.section_digest != expected_section_digest:
                        raise GovernanceIntakeError(
                            "STALE_SECTION_DIGEST", "semantic amendment target changed since it was inspected"
                        )
                try:
                    body, amendment = apply_plan_amendment(snapshot.body, document, amendment)
                except PlanSemanticError as exc:
                    raise GovernanceIntakeError(exc.code, exc.message) from exc
                try:
                    admission = preflight_local_plan_candidate(
                        candidate_kind=PlanCandidateKind.AMEND,
                        project_id=project_id,
                        plan_id=plan_id,
                        candidate_body=body,
                    )
                except LocalPlanLifecycleError as exc:
                    raise GovernanceIntakeError(exc.code, exc.message) from exc
                if admission.semantic_readiness != "READY":
                    codes = ", ".join(admission.finding_codes)
                    code = admission.finding_codes[0] if admission.finding_codes else "PLAN_CANDIDATE_NOT_READY"
                    raise GovernanceIntakeError(code, f"structured Plan amendment failed semantic admission: {codes}")
                amendment_digest = canonical_plan_amendment_digest(amendment)
                decision_receipt = self._materialize_plan_decision(
                    project_id=project_id,
                    plan_id=plan_id,
                    base_plan_revision=current.revision,
                    amendment=amendment,
                    amendment_digest=amendment_digest,
                )
                request = self._lifecycle_request(
                    mode=mode,
                    project_id=project_id,
                    plan_id=plan_id,
                    current=current,
                    candidate_body=body,
                    arguments=arguments,
                    authorization_reference=decision_receipt["decision_ref"],
                )
                event = {
                    "event": "plan_amendment",
                    "decision_ref": decision_receipt["decision_ref"],
                    "amendment": amendment,
                    "amendment_digest": amendment_digest,
                    "plan_decision_receipt": decision_receipt,
                }
                try:
                    result = LocalPlanLifecycleCoordinator(store, journal).amend(
                        request,
                        body,
                        expected_authority=current.authority,
                        amendment_event=event,
                    )
                except LocalPlanLifecycleError as exc:
                    raise GovernanceIntakeError(exc.code, exc.message) from exc
                if not result.ok:
                    raise GovernanceIntakeError(result.error_code or "PLAN_AMEND_FAILED", result.error_message or "Plan amendment failed")
                payload = self._plan_result(
                    mode,
                    project_id,
                    plan_id,
                    registration,
                    governance_root,
                    store,
                    source_repository=source_repository,
                    write_projection=True,
                )
                after_record = store.get_plan(project_id, plan_id)
                if after_record is None:
                    raise GovernanceIntakeError("PLAN_READBACK_FAILED", "amended Plan record is absent after mutation")
                after_snapshot, _after_document = self._read_bound_plan(governance_root, after_record)
                journal_entry = result.journal_entry
                journal_record = getattr(journal_entry, "record", None)
                journal_state = getattr(getattr(journal_record, "journal_state", None), "value", None)
                projection_receipt = payload.get("projection_receipt")
                payload["amendment_readback"] = {
                    "plan_revision_before": current.revision,
                    "plan_digest_before": snapshot.digest,
                    "semantic_decision_ref": decision_receipt["decision_ref"],
                    "semantic_decision_receipt": decision_receipt,
                    "amendment_digest": amendment_digest,
                    "plan_revision_after": after_record.revision,
                    "plan_digest_after": after_snapshot.digest,
                    "authority_source_revision_after": after_snapshot.revision,
                    "journal_cas_outcome": {
                        "journal_id": result.journal_id,
                        "journal_state": journal_state,
                        "phase": result.phase.value,
                        "plan_store_revision_before": current.revision,
                        "plan_store_revision_after": after_record.revision,
                        "authority_source_revision_before": snapshot.revision,
                        "authority_source_revision_after": after_snapshot.revision,
                        "authority_write_verified": result.ok,
                    },
                    "projection_reconciliation_status": (
                        "RECONCILED" if isinstance(projection_receipt, dict) else "NOT_RECONCILED"
                    ),
                }
                return payload
            if mode == "close":
                body = arguments.get("candidate_body")
                if not isinstance(body, str) or not body.strip():
                    raise GovernanceIntakeError(
                        "PLAN_BODY_REQUIRED",
                        "Plan close requires a trusted terminal candidate_body",
                    )
                current = store.get_plan(project_id, plan_id)
                expected_revision = arguments.get("expected_plan_revision")
                if (
                    isinstance(expected_revision, bool)
                    or not isinstance(expected_revision, int)
                    or expected_revision < 1
                ):
                    raise GovernanceIntakeError(
                        "PLAN_REVISION_REQUIRED",
                        "Plan close requires a positive expected_plan_revision",
                    )
                if current is None:
                    raise GovernanceIntakeError("PLAN_RECORD_REQUIRED", "Plan close requires an existing Plan record")
                raw_evidence = arguments.get("closure_evidence")
                if isinstance(raw_evidence, PlanCloseMaterializationEvidence):
                    closure_evidence = raw_evidence
                elif isinstance(raw_evidence, Mapping):
                    try:
                        closure_evidence = PlanCloseMaterializationEvidence(**dict(raw_evidence))
                    except (TypeError, ValueError, LocalPlanLifecycleError) as exc:
                        raise GovernanceIntakeError(
                            "CLOSURE_EVIDENCE_INVALID", str(exc)
                        ) from exc
                else:
                    raise GovernanceIntakeError(
                        "CLOSURE_EVIDENCE_REQUIRED",
                        "Plan close requires trusted accepted closure evidence",
                    )
                request = self._lifecycle_request(
                    mode=mode,
                    project_id=project_id,
                    plan_id=plan_id,
                    current=current,
                    candidate_body=body,
                    arguments=arguments,
                )
                result = LocalPlanLifecycleCoordinator(store, journal).close_local_plan(
                    request,
                    body,
                    expected_authority=current.authority,
                    closure_evidence=closure_evidence,
                )
                if not result.ok:
                    raise GovernanceIntakeError(
                        result.error_code or "PLAN_CLOSE_FAILED",
                        result.error_message or "Plan close failed",
                    )
                return self._plan_result(
                    mode,
                    project_id,
                    plan_id,
                    registration,
                    governance_root,
                    store,
                    source_repository=source_repository,
                    write_projection=True,
                )
            if store.get_plan(project_id, plan_id) is None:
                raise GovernanceIntakeError(
                    "PLAN_RECORD_REQUIRED",
                    "Plan inspection requires an existing Project Governance Plan record",
                )
            return self._inspect_plan(
                mode,
                project_id,
                plan_id,
                registration,
                governance_root,
                store,
                source_repository=source_repository,
                view=arguments.get("view", DEFAULT_PLAN_INSPECT_VIEW),
                section_ref=arguments.get("section_ref"),
            )
        finally:
            self._close_owner(journal)
            self._close_owner(store)

    def _project_id(self, project_id: object) -> str:
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            raise GovernanceIntakeError("PROJECT_ID_INVALID", "project_id must match the canonical project identity grammar")
        return project_id

    @staticmethod
    def _validate_amendment_scope(project_id: str, plan_id: str, amendment: Mapping[str, Any]) -> None:
        if amendment.get("kind") == "waive_m2_rv2_gate" and (
            project_id != "aota_forge"
            or plan_id != "plan_005_governance_self_hosting_routing_runtime_lifecycle"
        ):
            raise GovernanceIntakeError(
                "AMENDMENT_SCOPE_INVALID",
                "waive_m2_rv2_gate is authorized only for aota_forge Plan #005",
            )

    def _source_repository(self, supplied: object) -> str:
        trusted = self.context.source_repository.strip()
        if supplied is None:
            return trusted
        if not isinstance(supplied, str) or len(supplied) > 512 or not supplied.strip():
            raise GovernanceIntakeError("SOURCE_REPOSITORY_INVALID", "source_repository must be bounded text")
        candidate = supplied.strip()
        if trusted and candidate != trusted:
            raise GovernanceIntakeError("SOURCE_REPOSITORY_MISMATCH", "source_repository differs from trusted runtime context")
        return trusted or candidate

    def _prepare_manifest(self, project_id: str, arguments: Mapping[str, Any]) -> ProjectRegistrationEvidence | None:
        root = _trusted_dir(self.context.project_root, "project_root", required=False)
        if root is None:
            return None
        manifest = root / ".aota" / "project.yaml"
        if manifest.exists() or manifest.is_symlink():
            try:
                loaded_root, data = load_project(manifest)
            except Exception as exc:
                raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", str(exc)) from exc
            if loaded_root != root or data["project"]["id"] != project_id:
                raise GovernanceIntakeError("PROJECT_ID_MISMATCH", "existing manifest does not match project_id")
            return None
        capabilities = arguments.get("capabilities")
        if capabilities is not None and (
            not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities)
        ):
            raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", "capabilities must be a list of strings")
        data = _minimal_manifest(
            project_id,
            name=arguments.get("project_name") if isinstance(arguments.get("project_name"), str) else None,
            kind=arguments.get("project_kind") if isinstance(arguments.get("project_kind"), str) else None,
            summary=arguments.get("summary") if isinstance(arguments.get("summary"), str) else None,
            capabilities=capabilities,
        )
        try:
            _atomic_write_manifest(manifest, data)
            loaded_root, loaded = load_project(manifest)
        except GovernanceIntakeError:
            raise
        except Exception as exc:
            raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", str(exc)) from exc
        if loaded_root != root or loaded["project"]["id"] != project_id:
            raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", "created manifest did not verify")
        return None

    def _register_and_reconcile(
        self, project_id: str, source_repository: str
    ) -> ProjectRegistrationEvidence | ProjectReconciliationEvidence:
        if self.context.registry_path is None:
            raise GovernanceIntakeError("PROJECT_REGISTRY_REQUIRED", "trusted registry_path is required for Project intake")
        try:
            registration = register_project(
                workspace_id=self.context.workspace_id,
                registry_path=Path(self.context.registry_path),
                project_id=project_id,
                source_repository=source_repository or None,
                workspace_root=Path(self.context.workspace_root) if self.context.workspace_root else None,
            )
            reconciled = reconcile_project(
                workspace_id=self.context.workspace_id,
                registry_path=Path(self.context.registry_path),
                project_id=project_id,
                source_repository=source_repository or None,
                expected_project_root=registration.project_root,
                expected_manifest_path=registration.manifest_path,
            )
            self._resolved_project_root = reconciled.project_root
            return reconciled
        except GovernanceIntakeError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None) or "PROJECT_INTAKE_FAILED"
            raise GovernanceIntakeError(str(code), str(exc)) from exc

    def _reconcile_registered(
        self, project_id: str, source_repository: str
    ) -> ProjectReconciliationEvidence:
        if self.context.registry_path is None:
            raise GovernanceIntakeError(
                "PROJECT_REGISTRY_REQUIRED",
                "trusted registry_path is required for Project reconciliation",
            )
        expected_root = self._project_root(required=False)
        try:
            reconciled = reconcile_project(
                workspace_id=self.context.workspace_id,
                registry_path=Path(self.context.registry_path),
                project_id=project_id,
                source_repository=source_repository or None,
                expected_project_root=str(expected_root) if expected_root is not None else None,
            )
        except Exception as exc:
            code = getattr(exc, "code", None) or "PROJECT_RECONCILIATION_FAILED"
            raise GovernanceIntakeError(str(code), str(exc)) from exc
        self._resolved_project_root = reconciled.project_root
        return reconciled

    def _ensure_governance_root(
        self, project_id: str, *, create: bool = True
    ) -> LocalGovernanceRootBinding:
        base = _trusted_dir(self.context.governance_base, "governance_base")
        assert base is not None
        child = base / project_id
        if child.is_symlink():
            raise GovernanceIntakeError("LOCAL_GOVERNANCE_ROOT_INVALID", "project governance root must not be a symlink")
        if create:
            try:
                child.mkdir(mode=0o755, exist_ok=True)
            except OSError as exc:
                raise GovernanceIntakeError("LOCAL_GOVERNANCE_ROOT_INVALID", str(exc)) from exc
        elif not child.exists():
            raise GovernanceIntakeError(
                "LOCAL_GOVERNANCE_ROOT_INVALID",
                "project governance root does not exist",
            )
        if not child.is_dir():
            raise GovernanceIntakeError("LOCAL_GOVERNANCE_ROOT_INVALID", "project governance root is not a directory")
        try:
            return LocalGovernanceRootBinding.from_trusted_base(base, project_id=project_id)
        except Exception as exc:
            raise GovernanceIntakeError("LOCAL_GOVERNANCE_ROOT_INVALID", str(exc)) from exc

    def _project_root(self, *, required: bool = True) -> Path | None:
        raw = self._resolved_project_root or self.context.project_root
        return _trusted_dir(raw, "project_root", required=required)

    def _open_owners(self, project_id: str) -> tuple[SQLiteProjectGovernanceStore, FileBackedDurableJournalStore]:
        project_root = self._project_root(required=False)
        if project_root is None:
            raise GovernanceIntakeError("PROJECT_ROOT_REQUIRED", "trusted project_root is required for Plan intake")
        state_dir = project_root / ".aota"
        if state_dir.is_symlink():
            raise GovernanceIntakeError("PROJECT_STATE_INVALID", ".aota must not be a symlink")
        state_dir.mkdir(mode=0o755, exist_ok=True)
        store_path = Path(self.context.governance_store_path or state_dir / "governance.sqlite3")
        journal_path = Path(self.context.journal_path or state_dir / "governance-journal.json")
        try:
            store = SQLiteProjectGovernanceStore(store_path)
            journal = FileBackedDurableJournalStore(journal_path)
        except Exception as exc:
            raise GovernanceIntakeError("GOVERNANCE_STORE_OPEN_FAILED", str(exc)) from exc
        return store, journal

    def _destination(self, root: LocalGovernanceRootBinding, plan_id: str) -> LocalPlanAuthorityDestination:
        return LocalPlanAuthorityDestination(root, plan_id, _target_ref(plan_id))

    def _read_bound_plan(
        self,
        root: LocalGovernanceRootBinding,
        record: ProjectPlanRecord,
    ) -> tuple[Any, Any]:
        if record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            raise GovernanceIntakeError("LOCAL_AUTHORITY_REQUIRED", "Plan operation requires local Plan authority")
        try:
            adapter = LocalPlanAuthorityReadAdapter(
                self._destination(root, record.plan_id), binding=record.authority
            )
            snapshot = adapter.load()
            if record.authority.source_revision is not None and snapshot.revision != record.authority.source_revision:
                raise GovernanceIntakeError("STALE_AUTHORITY", "local Plan revision changed during intake")
            if record.authority.source_digest is not None and snapshot.digest != record.authority.source_digest:
                raise GovernanceIntakeError("STALE_AUTHORITY", "local Plan digest changed during intake")
            document = normalize_portable_plan(
                snapshot.body,
                source_revision=str(snapshot.revision) if snapshot.revision is not None else None,
                source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
            )
            return snapshot, document
        except GovernanceIntakeError:
            raise
        except Exception as exc:
            raise GovernanceIntakeError("PLAN_READ_FAILED", str(exc)) from exc

    def _inspect_plan(
        self,
        mode: str,
        project_id: str,
        plan_id: str,
        registration: ProjectRegistrationEvidence | ProjectReconciliationEvidence,
        root: LocalGovernanceRootBinding,
        store: SQLiteProjectGovernanceStore,
        *,
        source_repository: str,
        view: str,
        section_ref: str | None,
    ) -> dict[str, Any]:
        record = store.get_plan(project_id, plan_id)
        if record is None:
            raise GovernanceIntakeError("PLAN_RECORD_REQUIRED", "Plan inspection requires a current Plan record")
        snapshot, document = self._read_bound_plan(root, record)
        source, route = self._source(project_id, root, store, requested_plan_id=plan_id)
        plan_input = next((item for item in source.plan_documents if item.plan_id == plan_id), None)
        if plan_input is None or plan_input.document.source_digest != document.source_digest:
            raise GovernanceIntakeError("STALE_AUTHORITY", "Plan projection did not read the same current authority")
        lifecycle = GovernanceProjectionLifecycle(governance_store=store, governance_root=root)
        bundle = lifecycle.engine.rebuild(source)
        context_route = build_context_route(replace(route, bundle=bundle))
        plan_card = next((card for card in bundle.plans if card.plan_id == plan_id), None)
        if plan_card is None:
            raise GovernanceIntakeError("PLAN_CARD_MISSING", "current Plan Card could not be projected")
        milestone_card = None
        if document.current_milestone is not None:
            milestone_card = next(
                (
                    card
                    for card in bundle.milestones
                    if card.plan_id == plan_id and card.milestone_id == document.current_milestone
                ),
                None,
            )
        base: dict[str, Any] = {
            "status": "INSPECTED",
            "mode": mode,
            "view": view,
            "project_id": project_id,
            "plan_id": plan_id,
            "plan_status": document.plan_status,
            "current_milestone": document.current_milestone,
            "current_status": document.current_status,
            "current_blocker": document.current_blocker,
            "plan_revision": record.revision,
            "plan_lifecycle_state": record.lifecycle_state,
            "authority_source_kind": record.authority.source_kind,
            "authority_source_revision": snapshot.revision,
            "source_digest": snapshot.digest,
            "plan_card": plan_card.to_dict(),
            "milestone_card": milestone_card.to_dict() if milestone_card is not None else None,
            "navigation_refs": [ref.to_dict() for ref in plan_card.navigation_refs],
            "context_route": context_route.to_dict(),
            "project": _public_project_evidence(registration),
            "source_repository": source_repository,
        }
        if view == "card":
            return base
        if view in {"index", "section"}:
            semantic_index = PlanSemanticIndex.build(
                project_id=project_id,
                plan_id=plan_id,
                plan_revision=record.revision,
                authority_revision=snapshot.revision,
                source_digest=snapshot.digest or "",
                body=snapshot.body,
                document=document,
            )
        if view == "index":
            return {
                **base,
                "semantic_index": semantic_index.to_dict(),
            }
        if view == "section":
            try:
                section = semantic_index.resolve(section_ref or "")
            except PlanSemanticError as exc:
                raise GovernanceIntakeError(exc.code, exc.message) from exc
            if len(section.content.encode("utf-8")) > MAX_PLAN_SECTION_READ_BYTES:
                raise GovernanceIntakeError(
                    "PLAN_SECTION_TOO_LARGE",
                    f"requested semantic section exceeds {MAX_PLAN_SECTION_READ_BYTES} bytes",
                )
            return {
                "status": "INSPECTED",
                "mode": mode,
                "view": view,
                "project_id": project_id,
                "plan_id": plan_id,
                "plan_revision": record.revision,
                "authority_source_revision": snapshot.revision,
                "source_digest": snapshot.digest,
                "section": {**section.index_dict(), "content": section.content},
                "line_number_is_authority": False,
            }
        if view == "full":
            return {
                **base,
                "normalized_plan": document.to_dict(),
                "plan_body": snapshot.body,
                "read_through": "LocalPlanAuthorityReadAdapter",
            }
        raise GovernanceIntakeError("PLAN_VIEW_INVALID", f"unsupported Plan inspection view {view!r}")

    def _materialize_plan_decision(
        self,
        *,
        project_id: str,
        plan_id: str,
        base_plan_revision: int,
        amendment: Mapping[str, Any],
        amendment_digest: str,
    ) -> dict[str, Any]:
        decision_kind = "plan_amendment"
        decision_identity = {
            "decision_kind": decision_kind,
            "project_id": project_id,
            "plan_id": plan_id,
            "base_plan_revision": base_plan_revision,
            "amendment_digest": amendment_digest,
            "decision_source": self.decision_source,
        }
        receipt = {
            **decision_identity,
            "amendment": dict(amendment),
            "request_provenance": dict(self.decision_source),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "decision_ref": f"plan-amendment:{_canonical_digest(decision_identity)}",
        }
        return receipt

    def _read_plan_documents(
        self,
        root: LocalGovernanceRootBinding,
        records: tuple[ProjectPlanRecord, ...],
    ) -> tuple[PlanDocumentInput, ...]:
        documents: list[PlanDocumentInput] = []
        for record in records:
            if record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
                continue
            try:
                adapter = LocalPlanAuthorityReadAdapter(
                    self._destination(root, record.plan_id), binding=record.authority
                )
                snapshot = adapter.load()
                if record.authority.source_revision is not None and snapshot.revision != record.authority.source_revision:
                    raise GovernanceIntakeError("STALE_AUTHORITY", "local Plan revision changed during intake")
                if record.authority.source_digest is not None and snapshot.digest != record.authority.source_digest:
                    raise GovernanceIntakeError("STALE_AUTHORITY", "local Plan digest changed during intake")
                document = load_local_portable_plan(adapter)
            except GovernanceIntakeError:
                raise
            except Exception as exc:
                raise GovernanceIntakeError("PLAN_READ_FAILED", str(exc)) from exc
            documents.append(
                plan_document_input(
                    plan_id=record.plan_id,
                    document=document,
                    current_milestone=document.current_milestone,
                    authority=record.authority,
                )
            )
        return tuple(documents)

    def _source(
        self,
        project_id: str,
        root: LocalGovernanceRootBinding,
        store: SQLiteProjectGovernanceStore,
        *,
        requested_plan_id: str | None = None,
    ) -> tuple[GovernanceProjectionInput, ContextRouteInput]:
        project_root = self._project_root()
        assert project_root is not None
        try:
            _manifest_root, manifest = load_project(project_root / ".aota" / "project.yaml")
        except Exception as exc:
            raise GovernanceIntakeError("PROJECT_MANIFEST_INVALID", str(exc)) from exc
        records = tuple(store.list_plans(project_id))
        documents = self._read_plan_documents(root, records)
        selected = next((item for item in records if item.plan_id == requested_plan_id), None)
        route = ContextRouteInput(
            project_id=project_id,
            plan_id=selected.plan_id if selected else None,
            authority_source_kind=selected.authority.source_kind if selected else None,
            authority_ref=selected.authority.authority_ref if selected else None,
            authority_revision=(str(selected.authority.source_revision) if selected and selected.authority.source_revision is not None else None),
            authority_digest=selected.authority.source_digest if selected else None,
            authorized_root_refs=(("local-governance", ("read", "search")),),
        )
        source = GovernanceProjectionInput(
            project_id=project_id,
            project_ref=f"project:{project_id}",
            project_status=manifest["project"]["status"],
            governance_root_refs=("local-governance",),
            plan_records=records,
            plan_documents=documents,
        )
        return source, route

    def _project_result(
        self,
        mode: str,
        project_id: str,
        source_repository: str,
        registration: ProjectRegistrationEvidence | ProjectReconciliationEvidence,
        *,
        write_projection: bool,
    ) -> dict[str, Any]:
        root = self._ensure_governance_root(project_id, create=write_projection)
        store, journal = self._open_owners(project_id)
        try:
            source, route = self._source(project_id, root, store)
            lifecycle = GovernanceProjectionLifecycle(
                governance_store=store,
                governance_root=root,
            )
            if write_projection:
                refreshed = lifecycle.refresh_for_trigger(
                    REFRESH_TRIGGER_PLAN_MUTATION,
                    source,
                    context_route_input=route,
                )
                bundle = refreshed.bundle
                context_route = refreshed.context_route
                receipt = refreshed.receipt.to_dict()
            else:
                bundle = lifecycle.engine.rebuild(source)
                context_route = build_context_route(replace(route, bundle=bundle))
                receipt = None
            return self._result_payload(
                mode=mode,
                project_id=project_id,
                registration=registration,
                source_repository=source_repository,
                bundle=bundle,
                context_route=context_route,
                receipt=receipt,
            )
        except GovernanceIntakeError:
            raise
        except Exception as exc:
            code = getattr(exc, "code", None) or "GOVERNANCE_PROJECTION_FAILED"
            raise GovernanceIntakeError(str(code), str(exc)) from exc
        finally:
            self._close_owner(journal)
            self._close_owner(store)

    def _plan_result(
        self,
        mode: str,
        project_id: str,
        plan_id: str,
        registration: ProjectRegistrationEvidence | ProjectReconciliationEvidence,
        root: LocalGovernanceRootBinding,
        store: SQLiteProjectGovernanceStore,
        *,
        source_repository: str,
        write_projection: bool,
    ) -> dict[str, Any]:
        source, route = self._source(project_id, root, store, requested_plan_id=plan_id)
        lifecycle = GovernanceProjectionLifecycle(governance_store=store, governance_root=root)
        if write_projection:
            refreshed = lifecycle.refresh_for_trigger(
                REFRESH_TRIGGER_PLAN_MUTATION,
                source,
                context_route_input=route,
            )
            bundle = refreshed.bundle
            context_route = refreshed.context_route
            receipt = refreshed.receipt.to_dict()
        else:
            bundle = lifecycle.engine.rebuild(source)
            context_route = build_context_route(replace(route, bundle=bundle))
            receipt = None
        return self._result_payload(
            mode=mode,
            project_id=project_id,
            plan_id=plan_id,
            registration=registration,
            source_repository=source_repository,
            bundle=bundle,
            context_route=context_route,
            receipt=receipt,
        )

    def _result_payload(
        self,
        *,
        mode: str,
        project_id: str,
        registration: ProjectRegistrationEvidence | ProjectReconciliationEvidence,
        source_repository: str,
        bundle: Any,
        context_route: Any,
        receipt: dict[str, Any] | None,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        plans = tuple(bundle.plans)
        milestones = tuple(bundle.milestones)
        return {
            "status": "INSPECTED" if mode == "inspect" else "VERIFIED",
            "mode": mode,
            "project_id": project_id,
            "plan_id": plan_id,
            "source_repository": source_repository,
            "project": _public_project_evidence(registration),
            "projection": bundle.to_dict(),
            "context_route": context_route.to_dict(),
            "projection_receipt": receipt,
            "proof": {
                "project_card": bundle.project.complete,
                "plan_cards": {card.plan_id: card.complete for card in plans},
                "milestone_cards": {
                    f"{card.plan_id}:{card.milestone_id}": card.complete for card in milestones
                },
                "context_route": True,
            },
        }

    def _lifecycle_request(
        self,
        *,
        mode: str,
        project_id: str,
        plan_id: str,
        current: ProjectPlanRecord | None,
        candidate_body: str,
        arguments: Mapping[str, Any],
        authorization_reference: str | None = None,
    ) -> LocalPlanLifecycleRequest:
        candidate_digest = _digest(candidate_body)
        identity = _canonical_digest(
            {
                "operation": GOVERNANCE_PLAN_OPERATION,
                "mode": mode,
                "project_id": project_id,
                "plan_id": plan_id,
                "candidate_digest": candidate_digest,
            }
        )
        key = arguments.get("idempotency_key")
        key = _safe_id(key, "idempotency_key") if key is not None else f"governance-{mode}-{identity[:32]}"
        correlation = arguments.get("correlation_id")
        correlation = _safe_id(correlation, "correlation_id") if correlation is not None else f"governance-{identity[:32]}"
        authorization = authorization_reference or arguments.get("authorization_reference") or "governance-intake"
        lease = arguments.get("lease_reference") or f"lease-{identity[:32]}"
        expected = arguments.get("expected_plan_revision")
        if expected is None and current is not None:
            expected = current.revision
        if mode == "amend" and current is not None and expected != current.revision:
            raise GovernanceIntakeError("STALE_PLAN_REVISION", "amendment expected_plan_revision is stale")
        try:
            return LocalPlanLifecycleRequest(
                project_id=project_id,
                plan_id=plan_id,
                destination=self._destination(self._ensure_governance_root(project_id), plan_id),
                principal=self.principal,
                authorization_reference=_safe_id(authorization, "authorization_reference"),
                lease_reference=_safe_id(lease, "lease_reference"),
                idempotency_key=key,
                correlation_id=correlation,
                intent_fingerprint=identity,
                expected_plan_revision=expected,
            )
        except LocalPlanLifecycleError as exc:
            raise GovernanceIntakeError(exc.code, exc.message) from exc

    @staticmethod
    def _close_owner(owner: Any) -> None:
        try:
            owner.close()
        except Exception:
            pass


def dispatch_governance_operation(
    operation: str,
    arguments: Mapping[str, Any],
    *,
    context: GovernanceIntakeContext | None,
    trusted_context: TrustedContext | None,
) -> Any:
    """Dispatch one canonical governance family through the existing ToolResponse seam."""
    from aota_forge.core.providers.tool import ToolResponse

    if not isinstance(context, GovernanceIntakeContext):
        return ToolResponse.failure(
            {"code": "AUTHORITY_DENIED", "message": "trusted governance intake context is absent"}
        )
    if not isinstance(trusted_context, TrustedContext) or not trusted_context.is_bound:
        return ToolResponse.failure(
            {"code": "AUTHORITY_DENIED", "message": "trusted runtime context is required for governance intake"}
        )
    principal = trusted_context.principal.id if trusted_context.principal is not None else "unbound"
    try:
        principal_context = trusted_context.principal
        decision_source = {
            "owner_type": principal_context.kind if principal_context is not None else "trusted_runtime",
            "owner_id": principal_context.id if principal_context is not None else "trusted-runtime",
            "channel": trusted_context.channel,
            "provenance": trusted_context.provenance,
        }
        if trusted_context.freshness:
            decision_source["freshness"] = trusted_context.freshness
        service = GovernanceIntakeService(
            context,
            principal=principal,
            decision_source=decision_source,
        )
        if operation == GOVERNANCE_PLAN_PREFLIGHT_OPERATION:
            project_id = arguments.get("project_id")
            project_root = _trusted_dir(context.project_root, "project_root", required=False)
            if project_root is None:
                raise GovernanceIntakeError(
                    "PROJECT_ROOT_REQUIRED", "trusted project_root is required for agent-facing Plan preflight"
                )
            manifest_path = project_root / ".aota" / "project.yaml"
            try:
                manifest_root, manifest = load_project(manifest_path)
            except Exception as exc:
                raise GovernanceIntakeError(
                    "PROJECT_MANIFEST_INVALID", "trusted Project manifest could not be verified"
                ) from exc
            if manifest_root != project_root or manifest["project"]["id"] != project_id:
                raise GovernanceIntakeError(
                    "PROJECT_ID_MISMATCH", "candidate Project does not match the trusted Project context"
                )
            result = service.preflight_plan_candidate(
                candidate_kind=arguments.get("candidate_kind"),
                project_id=arguments.get("project_id"),
                plan_id=arguments.get("plan_id"),
                candidate_body=arguments.get("candidate_body"),
            ).to_dict()
        elif operation == GOVERNANCE_PROJECT_OPERATION:
            mode = arguments.get("mode")
            if not isinstance(mode, str):
                raise GovernanceIntakeError("MODE_REQUIRED", "governance operation requires mode")
            payload = dict(arguments)
            payload.pop("mode", None)
            payload.pop("project_id", None)
            result = service.project(mode, project_id=arguments.get("project_id"), **payload)
        elif operation == GOVERNANCE_PLAN_OPERATION:
            mode = arguments.get("mode")
            if not isinstance(mode, str):
                raise GovernanceIntakeError("MODE_REQUIRED", "governance operation requires mode")
            payload = dict(arguments)
            payload.pop("mode", None)
            payload.pop("project_id", None)
            payload.pop("plan_id", None)
            result = service.plan(
                mode,
                project_id=arguments.get("project_id"),
                plan_id=arguments.get("plan_id"),
                **payload,
            )
        else:
            raise GovernanceIntakeError("UNKNOWN_OPERATION", f"unsupported governance operation: {operation!r}")
    except GovernanceIntakeError as exc:
        return ToolResponse.failure({"code": exc.code, "message": exc.message})
    except Exception as exc:  # noqa: BLE001 - typed boundary
        return ToolResponse.failure({"code": "GOVERNANCE_INTAKE_FAILED", "message": str(exc)[:512]})
    return ToolResponse.success(result)


__all__ = [
    "GOVERNANCE_PROJECT_OPERATION",
    "GOVERNANCE_PLAN_OPERATION",
    "GOVERNANCE_PLAN_PREFLIGHT_OPERATION",
    "GOVERNANCE_OPERATIONS",
    "GOVERNANCE_PROJECT_MODES",
    "GOVERNANCE_PLAN_MODES",
    "GOVERNANCE_PLAN_INSPECT_VIEWS",
    "DEFAULT_PLAN_INSPECT_VIEW",
    "STRUCTURED_PLAN_AMENDMENT_KINDS",
    "PROJECT_INTAKE_SINGLE_PATH",
    "PLAN_INTAKE_SINGLE_PATH",
    "PROJECT_INIT_REUSES_PROJECT_REGISTER",
    "PROJECT_INIT_REUSES_PROJECT_RECONCILE",
    "PLAN_INIT_REUSES_LOCAL_LIFECYCLE",
    "PLAN_INIT_TO_INITIAL_PROJECTION_WIRING",
    "MAP_MATERIALIZED_ON_INITIAL_INTAKE",
    "STATUS_MATERIALIZED_ON_INITIAL_INTAKE",
    "PROJECT_CARD_DERIVABLE",
    "PLAN_CARD_DERIVABLE",
    "MILESTONE_CARD_DERIVABLE",
    "CONTEXT_ROUTE_DERIVABLE",
    "SECOND_LIFECYCLE_IMPLEMENTATION",
    "SECOND_GOVERNANCE_INTAKE_PATH",
    "GovernanceIntakeError",
    "GovernanceIntakeContext",
    "GovernanceIntakeService",
    "dispatch_governance_operation",
]
