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
from dataclasses import dataclass, replace
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
    LocalPlanLifecycleCoordinator,
    LocalPlanLifecycleError,
    LocalPlanLifecycleRequest,
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
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


GOVERNANCE_PROJECT_OPERATION = "governance.project"
GOVERNANCE_PLAN_OPERATION = "governance.plan"
GOVERNANCE_OPERATIONS = (GOVERNANCE_PROJECT_OPERATION, GOVERNANCE_PLAN_OPERATION)
GOVERNANCE_PROJECT_MODES = frozenset({"init", "inspect", "reconcile"})
GOVERNANCE_PLAN_MODES = frozenset({"init", "inspect", "amend", "close"})

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

    def __init__(self, context: GovernanceIntakeContext, *, principal: str = "operator") -> None:
        if not isinstance(context, GovernanceIntakeContext):
            raise TypeError("context must be GovernanceIntakeContext")
        self.context = context
        self.principal = _safe_id(principal, "principal")
        self._resolved_project_root: str | None = None

    def project(self, mode: str, *, project_id: str, **arguments: Any) -> dict[str, Any]:
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

    def plan(self, mode: str, *, project_id: str, plan_id: str, **arguments: Any) -> dict[str, Any]:
        if mode not in GOVERNANCE_PLAN_MODES:
            raise GovernanceIntakeError("MODE_INVALID", f"unsupported governance.plan mode: {mode!r}")
        project_id = self._project_id(project_id)
        if not is_plan_id(plan_id):
            raise GovernanceIntakeError("PLAN_ID_INVALID", "plan_id must be one canonical internal Plan ID")
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
                result = LocalPlanLifecycleCoordinator(store, journal).initialize(
                    lifecycle_request, body
                )
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
                body = arguments.get("candidate_body")
                if not isinstance(body, str) or not body.strip():
                    raise GovernanceIntakeError("PLAN_BODY_REQUIRED", "plan amendment requires candidate_body")
                current = store.get_plan(project_id, plan_id)
                if current is None:
                    raise GovernanceIntakeError("PLAN_RECORD_REQUIRED", "Plan amendment requires an existing Plan record")
                request = self._lifecycle_request(
                    mode=mode,
                    project_id=project_id,
                    plan_id=plan_id,
                    current=current,
                    candidate_body=body,
                    arguments=arguments,
                )
                event = {
                    "event": "plan_amendment",
                    "decision_ref": arguments.get("semantic_decision_ref") or "governance-intake",
                }
                result = LocalPlanLifecycleCoordinator(store, journal).amend(
                    request,
                    body,
                    expected_authority=current.authority,
                    amendment_event=event,
                )
                if not result.ok:
                    raise GovernanceIntakeError(result.error_code or "PLAN_AMEND_FAILED", result.error_message or "Plan amendment failed")
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
            if mode == "close":
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
                result = LocalPlanLifecycleCoordinator(store, journal).retire(
                    project_id=project_id,
                    plan_id=plan_id,
                    expected_revision=expected_revision,
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
            return self._plan_result(
                mode,
                project_id,
                plan_id,
                registration,
                governance_root,
                store,
                source_repository=source_repository,
                write_projection=False,
            )
        finally:
            self._close_owner(journal)
            self._close_owner(store)

    def _project_id(self, project_id: object) -> str:
        if not isinstance(project_id, str) or not PROJECT_ID_RE.fullmatch(project_id):
            raise GovernanceIntakeError("PROJECT_ID_INVALID", "project_id must match the canonical project identity grammar")
        return project_id

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
    ) -> LocalPlanLifecycleRequest:
        candidate_digest = _digest(candidate_body)
        identity = _canonical_digest(
            {
                "operation": GOVERNANCE_PLAN_OPERATION,
                "mode": mode,
                "project_id": project_id,
                "plan_id": plan_id,
                "candidate_digest": candidate_digest,
                "semantic_decision_ref": arguments.get("semantic_decision_ref"),
            }
        )
        key = arguments.get("idempotency_key")
        key = _safe_id(key, "idempotency_key") if key is not None else f"governance-{mode}-{identity[:32]}"
        correlation = arguments.get("correlation_id")
        correlation = _safe_id(correlation, "correlation_id") if correlation is not None else f"governance-{identity[:32]}"
        authorization = arguments.get("authorization_reference") or arguments.get("semantic_decision_ref") or "governance-intake"
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
        service = GovernanceIntakeService(context, principal=principal)
        mode = arguments.get("mode")
        if not isinstance(mode, str):
            raise GovernanceIntakeError("MODE_REQUIRED", "governance operation requires mode")
        if operation == GOVERNANCE_PROJECT_OPERATION:
            payload = dict(arguments)
            payload.pop("mode", None)
            payload.pop("project_id", None)
            result = service.project(mode, project_id=arguments.get("project_id"), **payload)
        elif operation == GOVERNANCE_PLAN_OPERATION:
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
    "GOVERNANCE_OPERATIONS",
    "GOVERNANCE_PROJECT_MODES",
    "GOVERNANCE_PLAN_MODES",
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
