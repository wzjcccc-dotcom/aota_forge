"""Bounded Governance projection refresh and Architecture promotion lifecycle.

This module is the trusted Governance Core materialization seam for AF #57
M3/W2.  It deliberately has no watcher, queue, event bus, or second progress
store: callers submit one explicit ``ProjectionRefreshRequest`` and the
engine rebuilds the derived views from the existing truth-owner snapshots.

Architecture promotion uses a staged file, a durable receipt, the existing
Project Governance Store CAS, and read-back verification.  Filesystem and
SQLite writes cannot be one distributed transaction, so restart recovery is
explicit and fail-closed rather than claiming global atomicity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalGovernanceAdapterError,
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
)
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.governance.context_route import (
    ContextRoute,
    ContextRouteError,
    ContextRouteInput,
    build_context_route,
)
from aota_forge.governance.generated_views import (
    GeneratedViews,
    generate_views,
)
from aota_forge.governance.project_store import (
    ArchitectureMetadataRecord,
    ProjectPlanRecord,
    ProjectGovernanceStore,
    ProjectGovernanceStoreError,
    PLAN_LIFECYCLE_ACTIVE,
    architecture_authority_reference,
)
from aota_forge.governance.projection import (
    REFRESH_TRIGGER_ARCHITECTURE_PROMOTION,
    REFRESH_TRIGGER_WORK_RECONCILIATION,
    REFRESH_TRIGGERS,
    ArchitectureStateInput,
    GovernanceProjectionEngine,
    GovernanceProjectionError,
    GovernanceProjectionInput,
    GovernanceProjectionBundle,
    PlanDocumentInput,
    ProjectionRefreshRequest,
    architecture_state_from_plan_document,
    plan_document_input,
)
from aota_forge.runtime.task_main.coordinator_store import TaskMainCoordinatorStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


PROJECTION_LIFECYCLE_IMPLEMENTED = True
ARCHITECTURE_PROMOTION_IMPLEMENTED = True
PROJECTION_REFRESH_IS_EXPLICIT = True
BOUNDED_LIFECYCLE_TRIGGERS = True
PROGRESS_LIFECYCLE_HOOKS_WIRED = True
PROGRESS_CARD_IS_DERIVED = True
STATUS_IS_DERIVED = True
PROCESS_LOCAL_PROGRESS_AUTHORITY = False
RESTART_PROGRESS_TRUTH_EQUIVALENT = True
STATE_OWNERSHIP_PRESERVED = True
SECOND_PROGRESS_STORE_CREATED = False
ARCHITECTURE_CONTENT_DUPLICATED_IN_DB = False
SECOND_ARCHITECTURE_STORE_CREATED = False
ARCHITECTURE_MD_IS_SEMANTIC_AUTHORITY = True
ARCHITECTURE_DB_METADATA_IS_SEMANTIC_AUTHORITY = False
ARCHITECTURE_PROJECTION_REQUIRES_REAL_AUTHORITY_FILE = True
ARCHITECTURE_ACCEPTED_BASELINE_BINDING = True
ARCHITECTURE_INVALID_PROMOTION_FAILS_CLOSED = True
ARCHITECTURE_CAS_REQUIRED = True
VERIFY_AFTER_WRITE = True
PROMOTION_RECEIPT_DURABLE = True
PROMOTION_RESTART_RECOVERY = True
ARCHITECTURE_CARD_REFRESH_AFTER_PROMOTION = True
CONTEXT_ROUTE_REFRESH_AFTER_PROMOTION = True
MAP_REFRESH_AFTER_PROMOTION = True
STATUS_REFRESH_AFTER_PROMOTION = True
PROMOTION_WITHOUT_PROJECTION_SOURCE_DENIED = True
PGS_PLAN_STATE_RELOADED_DURING_PROMOTION = True
FINAL_PROMOTION_PRECONDITIONS_FROM_TRUSTED_STATE = True
CALLER_PROJECTION_SOURCE_IS_AUTHORITY = False
CALLER_AUTHORITY_OVERRIDE_COUNT = 0
PROGRESS_REFRESH_DEPENDS_ON_STEWARD = False
SECOND_PROJECTION_ENGINE_CREATED = False
GENERIC_WATCHER_CREATED = False
EVENT_BUS_CREATED = False

ARCHITECTURE_FILENAME = "ARCHITECTURE.md"
ARCHITECTURE_RECEIPT_FILENAME = ".architecture-promotion-receipt.json"
ARCHITECTURE_STAGE_FILENAME = ".architecture-promotion-stage.md"
MAX_ARCHITECTURE_BYTES = 512 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_PHASES = frozenset({"prepared", "committed", "aborted", "failed_closed"})
_ACCEPTED_BASELINE_STATUSES = frozenset({"accepted", "approved"})


class ProjectionLifecycleError(ValueError):
    """A projection or trusted materialization operation failed closed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProjectionLifecycleError("INVALID_DIGEST", f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_single_line(value: Any, label: str, *, max_length: int = 256) -> str:
    if (
        not isinstance(value, str)
        or type(value) is not str
        or not value.strip()
        or value != value.strip()
        or len(value) > max_length
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise ProjectionLifecycleError(
            "INVALID_REFERENCE", f"{label} must be a bounded single-line reference"
        )
    return value


def _require_optional_single_line(
    value: Any, label: str, *, max_length: int = 256
) -> str | None:
    if value is None:
        return None
    return _require_single_line(value, label, max_length=max_length)


def _read_file(path: Path, *, max_bytes: int) -> bytes:
    if path.is_symlink():
        raise ProjectionLifecycleError("SYMLINK_REJECTED", f"{path.name} must not be a symlink")
    if not path.exists():
        raise ProjectionLifecycleError("FILE_MISSING", f"required file {path.name} is missing")
    if not path.is_file():
        raise ProjectionLifecycleError("FILE_INVALID", f"{path.name} must be a regular file")
    try:
        if path.stat().st_size > max_bytes:
            raise ProjectionLifecycleError("FILE_TOO_LARGE", f"{path.name} exceeds the bounded size")
        with path.open("rb") as handle:
            value = handle.read(max_bytes + 1)
    except OSError as exc:
        raise ProjectionLifecycleError("FILE_READ_FAILED", f"cannot read {path.name}: {exc}") from exc
    if len(value) > max_bytes:
        raise ProjectionLifecycleError("FILE_TOO_LARGE", f"{path.name} exceeds the bounded size")
    return value


def _atomic_write(path: Path, data: bytes) -> None:
    if path.is_symlink():
        raise ProjectionLifecycleError("SYMLINK_REJECTED", f"{path.name} must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_name = ""
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = ""
    except OSError as exc:
        raise ProjectionLifecycleError("FILE_WRITE_FAILED", f"cannot write {path.name}: {exc}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _trusted_root(binding: LocalGovernanceRootBinding, project_id: str) -> Path:
    if not isinstance(binding, LocalGovernanceRootBinding):
        raise ProjectionLifecycleError(
            "TRUSTED_ROOT_REQUIRED", "Architecture materialization requires a LocalGovernanceRootBinding"
        )
    if binding.project_id != project_id:
        raise ProjectionLifecycleError(
            "PROJECT_MISMATCH", "trusted governance root does not match the promotion project"
        )
    try:
        binding.revalidate()
    except Exception as exc:
        raise ProjectionLifecycleError("TRUSTED_ROOT_INVALID", str(exc)) from exc
    return Path(binding.root_path)


def _architecture_bytes(content: Any) -> bytes:
    if not isinstance(content, str) or type(content) is not str or not content.strip():
        raise ProjectionLifecycleError("ARCHITECTURE_CONTENT_INVALID", "Architecture content must be non-empty text")
    if "\x00" in content:
        raise ProjectionLifecycleError("ARCHITECTURE_CONTENT_INVALID", "Architecture content must not contain NUL")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProjectionLifecycleError("ARCHITECTURE_CONTENT_INVALID", "Architecture content must be UTF-8") from exc
    if len(encoded) > MAX_ARCHITECTURE_BYTES:
        raise ProjectionLifecycleError("ARCHITECTURE_CONTENT_TOO_LARGE", "Architecture content exceeds the bounded size")
    return encoded


@dataclass(frozen=True)
class ProjectionRefreshReceipt:
    """Read-back proof for one explicit derived-view refresh."""

    project_id: str
    trigger: str
    projection_digest: str
    map_ref: str
    status_ref: str
    map_digest: str
    status_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ProjectionLifecycleError("INVALID_PROJECT_ID", "receipt project_id is not canonical")
        if self.trigger not in REFRESH_TRIGGERS:
            raise ProjectionLifecycleError("UNKNOWN_REFRESH_TRIGGER", "receipt trigger is not bounded")
        for value, label in (
            (self.projection_digest, "projection_digest"),
            (self.map_digest, "map_digest"),
            (self.status_digest, "status_digest"),
        ):
            _require_digest(value, label)
        _require_single_line(self.map_ref, "map_ref", max_length=512)
        _require_single_line(self.status_ref, "status_ref", max_length=512)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "trigger": self.trigger,
            "projection_digest": self.projection_digest,
            "map_ref": self.map_ref,
            "status_ref": self.status_ref,
            "map_digest": self.map_digest,
            "status_digest": self.status_digest,
        }


@dataclass(frozen=True)
class ProjectionRefreshResult:
    """The rebuilt Cards plus verified generated-view materialization."""

    request: ProjectionRefreshRequest
    bundle: GovernanceProjectionBundle
    views: GeneratedViews
    receipt: ProjectionRefreshReceipt
    context_route: ContextRoute
    map_path: Path
    status_path: Path


@dataclass(frozen=True)
class ArchitecturePromotionRequest:
    """Trusted input for one bounded Architecture Delta promotion."""

    project_id: str
    plan_id: str
    expected_revision: int
    expected_current_version: str
    expected_current_digest: str
    target_version: str
    content: str
    accepted_delta_ref: str
    accepted_delta_digest: str
    promotion_receipt_ref: str
    baseline_id: str | None = None
    baseline_status: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ProjectionLifecycleError("INVALID_PROJECT_ID", "project_id must be canonical")
        if not is_plan_id(self.plan_id):
            raise ProjectionLifecycleError("INVALID_PLAN_ID", "plan_id must be canonical")
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 1
        ):
            raise ProjectionLifecycleError("INVALID_REVISION", "expected_revision must be positive")
        for value, label in (
            (self.expected_current_version, "expected_current_version"),
            (self.target_version, "target_version"),
            (self.accepted_delta_ref, "accepted_delta_ref"),
            (self.promotion_receipt_ref, "promotion_receipt_ref"),
        ):
            _require_single_line(value, label)
        _require_digest(self.expected_current_digest, "expected_current_digest")
        _require_digest(self.accepted_delta_digest, "accepted_delta_digest")
        _architecture_bytes(self.content)
        if (self.accepted_delta_ref is None) != (self.accepted_delta_digest is None):
            raise ProjectionLifecycleError(
                "DELTA_PROVENANCE_INVALID", "accepted delta ref and digest must be supplied together"
            )
        _require_optional_single_line(self.baseline_id, "baseline_id", max_length=128)
        _require_optional_single_line(self.baseline_status, "baseline_status", max_length=64)

    @property
    def target_digest(self) -> str:
        return _digest_bytes(_architecture_bytes(self.content))

    @property
    def architecture_ref(self) -> str:
        return architecture_authority_reference(self.project_id)


@dataclass(frozen=True)
class ArchitecturePromotionReceipt:
    """Durable, digest-bound proof of an Architecture promotion attempt."""

    receipt_id: str
    project_id: str
    plan_id: str
    promotion_receipt_ref: str
    phase: str
    expected_revision: int
    resulting_revision: int | None
    expected_current_version: str
    expected_current_digest: str
    target_version: str
    target_digest: str
    accepted_delta_ref: str
    accepted_delta_digest: str
    architecture_ref: str
    baseline_id: str | None = None
    baseline_status: str | None = None
    staged_filename: str | None = None
    detail: str | None = None
    receipt_digest: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.receipt_id, "receipt_id"),
            (self.project_id, "project_id"),
            (self.plan_id, "plan_id"),
            (self.promotion_receipt_ref, "promotion_receipt_ref"),
            (self.architecture_ref, "architecture_ref"),
            (self.target_version, "target_version"),
            (self.expected_current_version, "expected_current_version"),
            (self.accepted_delta_ref, "accepted_delta_ref"),
        ):
            _require_single_line(value, label, max_length=512)
        if not PROJECT_ID_RE.fullmatch(self.project_id):
            raise ProjectionLifecycleError("INVALID_PROJECT_ID", "receipt project_id is not canonical")
        if not is_plan_id(self.plan_id):
            raise ProjectionLifecycleError("INVALID_PLAN_ID", "receipt plan_id is not canonical")
        if self.phase not in _RECEIPT_PHASES:
            raise ProjectionLifecycleError("INVALID_RECEIPT_PHASE", "receipt phase is not supported")
        for value, label in (
            (self.expected_current_digest, "expected_current_digest"),
            (self.target_digest, "target_digest"),
            (self.accepted_delta_digest, "accepted_delta_digest"),
        ):
            _require_digest(value, label)
        for value, label in ((self.expected_revision, "expected_revision"),):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ProjectionLifecycleError("INVALID_REVISION", f"{label} must be positive")
        if self.resulting_revision is not None and (
            isinstance(self.resulting_revision, bool)
            or not isinstance(self.resulting_revision, int)
            or self.resulting_revision < 1
        ):
            raise ProjectionLifecycleError("INVALID_REVISION", "resulting_revision must be positive or None")
        if self.staged_filename is not None:
            _require_single_line(self.staged_filename, "staged_filename", max_length=128)
        _require_optional_single_line(self.baseline_id, "baseline_id", max_length=128)
        _require_optional_single_line(self.baseline_status, "baseline_status", max_length=64)
        if self.detail is not None:
            _require_single_line(self.detail, "detail", max_length=512)
        if self.receipt_digest and not _SHA256_RE.fullmatch(self.receipt_digest):
            raise ProjectionLifecycleError("INVALID_DIGEST", "receipt_digest must be a SHA-256 digest")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "promotion_receipt_ref": self.promotion_receipt_ref,
            "phase": self.phase,
            "expected_revision": self.expected_revision,
            "resulting_revision": self.resulting_revision,
            "expected_current_version": self.expected_current_version,
            "expected_current_digest": self.expected_current_digest,
            "target_version": self.target_version,
            "target_digest": self.target_digest,
            "accepted_delta_ref": self.accepted_delta_ref,
            "accepted_delta_digest": self.accepted_delta_digest,
            "architecture_ref": self.architecture_ref,
            "baseline_id": self.baseline_id,
            "baseline_status": self.baseline_status,
            "staged_filename": self.staged_filename,
            "detail": self.detail,
        }

    def compute_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical_dict()).encode("utf-8")).hexdigest()

    def with_digest(self) -> "ArchitecturePromotionReceipt":
        return replace(self, receipt_digest=self.compute_digest())

    def to_dict(self) -> dict[str, Any]:
        payload = self.canonical_dict()
        payload["receipt_digest"] = self.receipt_digest
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchitecturePromotionReceipt":
        if not isinstance(data, Mapping):
            raise ProjectionLifecycleError("RECEIPT_INVALID", "receipt payload must be a mapping")
        expected = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        if set(data) != expected:
            raise ProjectionLifecycleError("RECEIPT_INVALID", "receipt fields are not canonical")
        receipt = cls(**dict(data))
        if receipt.receipt_digest != receipt.compute_digest():
            raise ProjectionLifecycleError("RECEIPT_DIGEST_MISMATCH", "receipt digest does not verify")
        return receipt


@dataclass(frozen=True)
class ArchitecturePromotionResult:
    status: str
    metadata: ArchitectureMetadataRecord
    receipt: ArchitecturePromotionReceipt
    architecture_path: Path
    projection: ProjectionRefreshResult | None = None


def _receipt_id(request: ArchitecturePromotionRequest) -> str:
    value = (
        f"{request.project_id}:{request.plan_id}:{request.expected_revision}:"
        f"{request.target_version}:{request.target_digest}"
    )
    return f"architecture-promotion-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]}"


class GovernanceProjectionLifecycle:
    """Trusted explicit refresh/promotion operations for one project."""

    def __init__(
        self,
        *,
        engine: GovernanceProjectionEngine | None = None,
        governance_store: ProjectGovernanceStore | None = None,
        governance_root: LocalGovernanceRootBinding | None = None,
        coordinator_store: TaskMainCoordinatorStore | None = None,
        execution_store: ExecutionStateStore | None = None,
    ) -> None:
        self.engine = engine or GovernanceProjectionEngine()
        self.governance_store = governance_store
        self.governance_root = governance_root
        self.coordinator_store = coordinator_store
        self.execution_store = execution_store

    def refresh(
        self,
        request: ProjectionRefreshRequest,
        *,
        project_directory: str | Path | None = None,
        context_route_input: ContextRouteInput | None = None,
    ) -> ProjectionRefreshResult:
        if not isinstance(request, ProjectionRefreshRequest):
            raise ProjectionLifecycleError("INVALID_INPUT", "request must be a ProjectionRefreshRequest")
        source = self._bind_architecture_source(request.source)
        request = replace(request, source=source)
        target = self._project_directory(project_directory, source.project_id)
        bundle, views, context_route = self._build_projection(
            request,
            context_route_input=context_route_input,
        )
        map_path = target / "MAP.md"
        status_path = target / "STATUS.md"
        map_bytes = views.map_markdown.encode("utf-8")
        status_bytes = views.status_markdown.encode("utf-8")
        _atomic_write(map_path, map_bytes)
        _atomic_write(status_path, status_bytes)
        if _read_file(map_path, max_bytes=len(map_bytes)) != map_bytes:
            raise ProjectionLifecycleError("READ_BACK_MISMATCH", "MAP.md read-back did not match the generated view")
        if _read_file(status_path, max_bytes=len(status_bytes)) != status_bytes:
            raise ProjectionLifecycleError("READ_BACK_MISMATCH", "STATUS.md read-back did not match the generated view")
        receipt = ProjectionRefreshReceipt(
            project_id=request.source.project_id,
            trigger=request.trigger,
            projection_digest=bundle.projection_digest(),
            map_ref=f"local-governance/{request.source.project_id}/MAP.md",
            status_ref=f"local-governance/{request.source.project_id}/STATUS.md",
            map_digest=_digest_bytes(map_bytes),
            status_digest=_digest_bytes(status_bytes),
        )
        return ProjectionRefreshResult(
            request=request,
            bundle=bundle,
            views=views,
            receipt=receipt,
            context_route=context_route,
            map_path=map_path,
            status_path=status_path,
        )

    def _build_projection(
        self,
        request: ProjectionRefreshRequest,
        *,
        context_route_input: ContextRouteInput | None = None,
    ) -> tuple[GovernanceProjectionBundle, GeneratedViews, ContextRoute]:
        """Build all derived outputs without writing them."""
        try:
            bundle = self.engine.rebuild_refresh(request)
            views = generate_views(bundle)
            route_input = context_route_input or ContextRouteInput(
                project_id=request.source.project_id,
                bundle=bundle,
            )
            if route_input.project_id != request.source.project_id:
                raise ContextRouteError(
                    "PROJECT_MISMATCH",
                    "Context Route input belongs to another project",
                )
            context_route = build_context_route(replace(route_input, bundle=bundle))
        except ContextRouteError as exc:
            raise ProjectionLifecycleError(exc.code, exc.message) from exc
        except GovernanceProjectionError as exc:
            raise ProjectionLifecycleError(exc.code, exc.message) from exc
        return bundle, views, context_route

    def _bind_architecture_source(
        self,
        source: GovernanceProjectionInput,
    ) -> GovernanceProjectionInput:
        if source.architecture is None:
            return source
        metadata = self._metadata_or_none(source.project_id)
        if metadata is None:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_METADATA_MISSING",
                "an Architecture projection requires accepted Project Governance metadata",
            )
        baseline_id, baseline_status = self._accepted_baseline_binding(
            project_id=source.project_id,
            plan_id=metadata.promoted_by_plan_id,
            plan_records=source.plan_records,
            plan_documents=source.plan_documents,
            architecture=source.architecture,
        )
        return replace(
            source,
            architecture=self._architecture_state_from_authority(
                metadata,
                baseline_id=baseline_id,
                baseline_status=baseline_status,
            ),
        )

    def refresh_for_trigger(
        self,
        trigger: str,
        source: GovernanceProjectionInput,
        *,
        project_directory: str | Path | None = None,
        context_route_input: ContextRouteInput | None = None,
    ) -> ProjectionRefreshResult:
        try:
            request = ProjectionRefreshRequest(trigger=trigger, source=source)
        except GovernanceProjectionError as exc:
            raise ProjectionLifecycleError(exc.code, exc.message) from exc
        return self.refresh(
            request,
            project_directory=project_directory,
            context_route_input=context_route_input,
        )

    def refresh_from_durable_owners(
        self,
        *,
        project_id: str,
        plan_documents: tuple[Any, ...] = (),
        architecture: ArchitectureStateInput | None = None,
        project_ref: str | None = None,
        project_status: str | None = None,
        governance_root_refs: tuple[str, ...] = (),
        trigger: str = REFRESH_TRIGGER_WORK_RECONCILIATION,
        project_directory: str | Path | None = None,
        context_route_input: ContextRouteInput | None = None,
        coordinator_store: TaskMainCoordinatorStore | None = None,
        execution_store: ExecutionStateStore | None = None,
    ) -> ProjectionRefreshResult:
        """Rebuild from the three existing durable truth owners.

        This is the explicit lifecycle consumer for accepted Work/Milestone
        transitions and restart reconstruction. It reads the Project
        Governance Store, Task Main coordinator store, and Execution State
        Store; it never copies any of them into a projection store.
        """
        governance_store = self.governance_store
        coordinator_owner = coordinator_store or self.coordinator_store
        execution_owner = execution_store or self.execution_store
        if governance_store is None or coordinator_owner is None or execution_owner is None:
            raise ProjectionLifecycleError(
                "DURABLE_OWNERS_REQUIRED",
                "refresh_from_durable_owners requires all three existing truth owners",
            )

        documents = tuple(plan_documents or ())
        try:
            plan_records = tuple(governance_store.list_plans(project_id))
            coordinator_states = tuple(
                state
                for state in coordinator_owner.list_all()
                if state.project_id == project_id
            )
            execution_records = tuple(execution_owner.list_all())
        except Exception as exc:
            raise ProjectionLifecycleError(
                "DURABLE_OWNER_READ_FAILED",
                f"cannot read durable projection owners: {exc}",
            ) from exc

        metadata = self._metadata_or_none(project_id)
        if metadata is not None:
            baseline_id, baseline_status = self._accepted_baseline_binding(
                project_id=project_id,
                plan_id=metadata.promoted_by_plan_id,
                plan_records=plan_records,
                plan_documents=documents,
                architecture=architecture,
            )
            architecture = self._architecture_state_from_authority(
                metadata,
                baseline_id=baseline_id,
                baseline_status=baseline_status,
            )
        elif architecture is not None:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_METADATA_MISSING",
                "an Architecture projection requires accepted Project Governance metadata",
            )

        source = GovernanceProjectionInput(
            project_id=project_id,
            project_ref=project_ref,
            project_status=project_status,
            governance_root_refs=governance_root_refs,
            plan_records=plan_records,
            plan_documents=documents,
            architecture=architecture,
            coordinator_states=coordinator_states,
            execution_records=execution_records,
        )
        return self.refresh_for_trigger(
            trigger,
            source,
            project_directory=project_directory,
            context_route_input=context_route_input,
        )

    def promote_architecture(
        self,
        request: ArchitecturePromotionRequest,
        *,
        projection_source: GovernanceProjectionInput | None = None,
        context_route_input: ContextRouteInput | None = None,
    ) -> ArchitecturePromotionResult:
        self._require_promotion_dependencies(request)
        root = _trusted_root(self.governance_root, request.project_id)  # type: ignore[arg-type]
        architecture_path = root / ARCHITECTURE_FILENAME
        receipt_path = root / ARCHITECTURE_RECEIPT_FILENAME
        stage_path = root / ARCHITECTURE_STAGE_FILENAME
        current = self._metadata(request.project_id)
        existing_receipt = self._read_receipt(receipt_path)

        if existing_receipt is not None and existing_receipt.phase == "prepared":
            if existing_receipt.receipt_id != _receipt_id(request):
                raise ProjectionLifecycleError(
                    "PROMOTION_IN_PROGRESS", "another Architecture promotion is awaiting recovery"
                )
            raise ProjectionLifecycleError(
                "PROMOTION_IN_PROGRESS", "recover the prepared Architecture promotion before retrying"
            )

        if self._metadata_matches_target(current, request):
            receipt = self._require_committed_receipt(existing_receipt, request)
            self._verify_architecture_file(architecture_path, request.target_digest)
            self._prepare_promoted_source(
                request=request,
                source=projection_source,
                metadata=current,
                architecture_digest=request.target_digest,
                context_route_input=context_route_input,
            )
            projection = self._refresh_promoted_source(
                projection_source,
                request,
                current,
                context_route_input=context_route_input,
            )
            if projection is None:
                raise ProjectionLifecycleError(
                    "PROMOTION_PROJECTION_REFRESH_FAILED",
                    "Architecture promotion did not refresh its projection",
                )
            return ArchitecturePromotionResult(
                status="already_applied",
                metadata=current,
                receipt=receipt,
                architecture_path=architecture_path,
                projection=projection,
            )

        self._preflight_promotion(
            request=request,
            source=projection_source,
            current=current,
            root=root,
            architecture_path=architecture_path,
            context_route_input=context_route_input,
        )
        staged_bytes = _architecture_bytes(request.content)
        _atomic_write(stage_path, staged_bytes)
        prepared = ArchitecturePromotionReceipt(
            receipt_id=_receipt_id(request),
            project_id=request.project_id,
            plan_id=request.plan_id,
            promotion_receipt_ref=request.promotion_receipt_ref,
            phase="prepared",
            expected_revision=request.expected_revision,
            resulting_revision=None,
            expected_current_version=request.expected_current_version,
            expected_current_digest=request.expected_current_digest,
            target_version=request.target_version,
            target_digest=request.target_digest,
            accepted_delta_ref=request.accepted_delta_ref,
            accepted_delta_digest=request.accepted_delta_digest,
            architecture_ref=request.architecture_ref,
            baseline_id=request.baseline_id,
            baseline_status=request.baseline_status,
            staged_filename=stage_path.name,
        ).with_digest()
        self._write_receipt(receipt_path, prepared)

        try:
            promoted = self.governance_store.compare_and_swap_architecture_metadata(  # type: ignore[union-attr]
                request.project_id,
                request.expected_revision,
                expected_current_version=request.expected_current_version,
                expected_current_digest=request.expected_current_digest,
                current_version=request.target_version,
                current_digest=request.target_digest,
                accepted_delta_ref=request.accepted_delta_ref,
                accepted_delta_digest=request.accepted_delta_digest,
                promotion_receipt_ref=request.promotion_receipt_ref,
                promoted_by_plan_id=request.plan_id,
            )
        except ProjectGovernanceStoreError as exc:
            self._remove_stage(stage_path)
            failed = replace(
                prepared,
                phase="failed_closed",
                staged_filename=None,
                detail=f"Architecture metadata CAS failed: {exc}",
            ).with_digest()
            self._write_receipt(receipt_path, failed)
            raise ProjectionLifecycleError("PROMOTION_CAS_FAILED", str(exc)) from exc

        try:
            if architecture_path.is_symlink():
                raise ProjectionLifecycleError("SYMLINK_REJECTED", "ARCHITECTURE.md must not be a symlink")
            os.replace(stage_path, architecture_path)
            self._verify_architecture_file(architecture_path, request.target_digest)
        except ProjectionLifecycleError:
            failed = replace(prepared, phase="failed_closed", resulting_revision=promoted.revision)
            self._write_receipt(receipt_path, failed.with_digest())
            raise
        except OSError as exc:
            failed = replace(
                prepared,
                phase="failed_closed",
                resulting_revision=promoted.revision,
                detail=f"Architecture materialization failed: {exc}",
            )
            self._write_receipt(receipt_path, failed.with_digest())
            raise ProjectionLifecycleError("ARCHITECTURE_MATERIALIZATION_FAILED", str(exc)) from exc

        committed = replace(
            prepared,
            phase="committed",
            resulting_revision=promoted.revision,
            staged_filename=None,
        ).with_digest()
        self._write_receipt(receipt_path, committed)
        try:
            projection = self._refresh_promoted_source(
                projection_source,
                request,
                promoted,
                context_route_input=context_route_input,
            )
        except ProjectionLifecycleError as exc:
            raise ProjectionLifecycleError(
                "PROMOTION_PROJECTION_REFRESH_FAILED",
                f"Architecture authority is durable but projection refresh is incomplete: {exc}",
            ) from exc
        if projection is None:
            raise ProjectionLifecycleError(
                "PROMOTION_PROJECTION_REFRESH_FAILED",
                "Architecture promotion did not refresh its projection",
            )
        return ArchitecturePromotionResult(
            status="promoted",
            metadata=promoted,
            receipt=committed,
            architecture_path=architecture_path,
            projection=projection,
        )

    def recover_architecture_promotion(
        self,
        *,
        projection_source: GovernanceProjectionInput | None = None,
        context_route_input: ContextRouteInput | None = None,
    ) -> ArchitecturePromotionResult | None:
        if self.governance_store is None or self.governance_root is None:
            raise ProjectionLifecycleError(
                "TRUSTED_PROMOTION_DEPENDENCIES_REQUIRED",
                "recovery requires the Project Governance Store and trusted root",
            )
        root = _trusted_root(self.governance_root, self.governance_root.project_id)
        receipt_path = root / ARCHITECTURE_RECEIPT_FILENAME
        stage_path = root / ARCHITECTURE_STAGE_FILENAME
        architecture_path = root / ARCHITECTURE_FILENAME
        receipt = self._read_receipt(receipt_path)
        if receipt is None:
            return None
        metadata = self._metadata(receipt.project_id)

        if receipt.phase == "committed":
            if not self._metadata_matches_receipt(metadata, receipt):
                raise ProjectionLifecycleError("RECOVERY_CONFLICT", "committed receipt and metadata disagree")
            self._verify_architecture_file(architecture_path, receipt.target_digest)
            projection = self._refresh_receipt_source(
                projection_source,
                receipt,
                metadata,
                context_route_input=context_route_input,
            )
            return ArchitecturePromotionResult("recovered", metadata, receipt, architecture_path, projection)

        if receipt.phase != "prepared":
            return ArchitecturePromotionResult("failed_closed", metadata, receipt, architecture_path, None)

        if self._metadata_matches_receipt(metadata, receipt):
            if stage_path.exists():
                if stage_path.is_symlink():
                    raise ProjectionLifecycleError("SYMLINK_REJECTED", "staged Architecture file must not be a symlink")
                try:
                    os.replace(stage_path, architecture_path)
                except OSError as exc:
                    raise ProjectionLifecycleError("RECOVERY_MATERIALIZATION_FAILED", str(exc)) from exc
            self._verify_architecture_file(architecture_path, receipt.target_digest)
            committed = replace(
                receipt,
                phase="committed",
                resulting_revision=metadata.revision,
                staged_filename=None,
            ).with_digest()
            self._write_receipt(receipt_path, committed)
            projection = self._refresh_receipt_source(
                projection_source,
                committed,
                metadata,
                context_route_input=context_route_input,
            )
            return ArchitecturePromotionResult("recovered", metadata, committed, architecture_path, projection)

        if (
            metadata.revision == receipt.expected_revision
            and metadata.current_version == receipt.expected_current_version
            and metadata.current_digest == receipt.expected_current_digest
        ):
            if stage_path.exists() and not stage_path.is_symlink():
                try:
                    stage_path.unlink()
                except OSError as exc:
                    raise ProjectionLifecycleError("RECOVERY_CLEANUP_FAILED", str(exc)) from exc
            aborted = replace(receipt, phase="aborted", staged_filename=None).with_digest()
            self._write_receipt(receipt_path, aborted)
            return ArchitecturePromotionResult("aborted", metadata, aborted, architecture_path, None)

        raise ProjectionLifecycleError(
            "RECOVERY_CONFLICT", "prepared Architecture receipt does not match current durable metadata"
        )

    def _project_directory(self, project_directory: str | Path | None, project_id: str) -> Path:
        if project_directory is None:
            if self.governance_root is None:
                raise ProjectionLifecycleError(
                    "PROJECT_DIRECTORY_REQUIRED", "generated views require an explicit trusted project directory"
                )
            return _trusted_root(self.governance_root, project_id)
        if not isinstance(project_directory, (str, Path)) or not str(project_directory).strip():
            raise ProjectionLifecycleError("PROJECT_DIRECTORY_INVALID", "project_directory must be non-empty")
        target = Path(project_directory)
        if target.is_symlink():
            raise ProjectionLifecycleError("SYMLINK_REJECTED", "project directory must not be a symlink")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProjectionLifecycleError("PROJECT_DIRECTORY_INVALID", str(exc)) from exc
        if not target.is_dir():
            raise ProjectionLifecycleError("PROJECT_DIRECTORY_INVALID", "project_directory must be a directory")
        return target

    def _require_promotion_dependencies(self, request: ArchitecturePromotionRequest) -> None:
        if not isinstance(request, ArchitecturePromotionRequest):
            raise ProjectionLifecycleError("INVALID_INPUT", "request must be an ArchitecturePromotionRequest")
        if self.governance_store is None or self.governance_root is None:
            raise ProjectionLifecycleError(
                "TRUSTED_PROMOTION_DEPENDENCIES_REQUIRED",
                "promotion requires the Project Governance Store and trusted root",
            )

    def _metadata(self, project_id: str) -> ArchitectureMetadataRecord:
        try:
            metadata = self.governance_store.get_architecture_metadata(project_id)  # type: ignore[union-attr]
        except ProjectGovernanceStoreError as exc:
            raise ProjectionLifecycleError("METADATA_READ_FAILED", str(exc)) from exc
        if metadata is None:
            raise ProjectionLifecycleError("ARCHITECTURE_METADATA_MISSING", f"no Architecture metadata for {project_id}")
        return metadata

    def _metadata_or_none(self, project_id: str) -> ArchitectureMetadataRecord | None:
        if self.governance_store is None:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_METADATA_REQUIRED",
                "an Architecture projection requires the Project Governance Store",
            )
        try:
            return self.governance_store.get_architecture_metadata(project_id)  # type: ignore[union-attr]
        except ProjectGovernanceStoreError as exc:
            raise ProjectionLifecycleError("METADATA_READ_FAILED", str(exc)) from exc

    def _promotion_plan_record(self, project_id: str, plan_id: str) -> ProjectPlanRecord:
        """Read the live Plan record used by the promotion preflight."""
        if self.governance_store is None:
            raise ProjectionLifecycleError(
                "TRUSTED_PROMOTION_DEPENDENCIES_REQUIRED",
                "promotion requires the Project Governance Store",
            )
        try:
            record = self.governance_store.get_plan(project_id, plan_id)
        except ProjectGovernanceStoreError as exc:
            raise ProjectionLifecycleError("PROMOTION_PLAN_READ_FAILED", str(exc)) from exc
        if record is None:
            raise ProjectionLifecycleError(
                "PROMOTION_PLAN_MISSING",
                f"no Project Governance Store Plan record exists for {project_id}/{plan_id}",
            )
        if record.project_id != project_id or record.plan_id != plan_id:
            raise ProjectionLifecycleError(
                "PROMOTION_PLAN_BINDING_MISMATCH",
                "Project Governance Store returned a Plan record for a different identity",
            )
        if record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            raise ProjectionLifecycleError(
                "PROMOTION_PLAN_NOT_ACTIVE",
                "Architecture promotion requires an active Project Governance Store Plan record",
            )
        return record

    def _trusted_plan_baseline(
        self,
        record: ProjectPlanRecord,
    ) -> tuple[str, str, PlanDocumentInput]:
        """Read the canonical Plan authority bound by the live PGS record."""
        if self.governance_root is None:
            raise ProjectionLifecycleError(
                "TRUSTED_PROMOTION_DEPENDENCIES_REQUIRED",
                "promotion requires a trusted governance root",
            )
        if record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_AUTHORITY_UNAVAILABLE",
                "the bound Plan authority has no trusted local document reader",
            )
        try:
            expected_ref = object_ref_subject(
                make_id(IdKind.SUBJECT, record.plan_id, sub_kind=SubjectKind.PLAN)
            )
            destination = LocalPlanAuthorityDestination(
                governance_root=self.governance_root,
                plan_id=record.plan_id,
                expected_ref=expected_ref,
            )
            if destination.authority_ref != record.authority.authority_ref:
                raise ProjectionLifecycleError(
                    "PROMOTION_PLAN_AUTHORITY_MISMATCH",
                    "the live Plan record is not bound to its trusted local Plan authority",
                )
            document = load_local_portable_plan(
                LocalPlanAuthorityReadAdapter(destination, binding=record.authority)
            )
        except ProjectionLifecycleError:
            raise
        except LocalGovernanceAdapterError as exc:
            raise ProjectionLifecycleError("PROMOTION_PLAN_AUTHORITY_READ_FAILED", str(exc)) from exc
        except Exception as exc:
            raise ProjectionLifecycleError("PROMOTION_PLAN_AUTHORITY_READ_FAILED", str(exc)) from exc

        if (
            record.authority.source_revision is not None
            and str(document.source_revision) != str(record.authority.source_revision)
        ):
            raise ProjectionLifecycleError(
                "PROMOTION_PLAN_AUTHORITY_STALE",
                "the canonical Plan document revision disagrees with the live Plan binding",
            )
        if (
            record.authority.source_digest is not None
            and document.source_digest != record.authority.source_digest
        ):
            raise ProjectionLifecycleError(
                "PROMOTION_PLAN_AUTHORITY_STALE",
                "the canonical Plan document digest disagrees with the live Plan binding",
            )

        trusted_input = plan_document_input(
            plan_id=record.plan_id,
            document=document,
            authority=record.authority,
        )
        try:
            baseline = self._accepted_baseline_binding(
                project_id=record.project_id,
                plan_id=record.plan_id,
                plan_records=(record,),
                plan_documents=(trusted_input,),
                architecture=None,
            )
        except ProjectionLifecycleError:
            raise
        except Exception as exc:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_BINDING_FAILED",
                str(exc),
            ) from exc
        return baseline[0], baseline[1], trusted_input

    def _validate_promotion_source(
        self,
        *,
        source: GovernanceProjectionInput | None,
        plan_record: ProjectPlanRecord,
        trusted_baseline: tuple[str, str],
        trusted_plan: PlanDocumentInput,
        metadata: ArchitectureMetadataRecord,
    ) -> None:
        """Treat projection input as a consistency check, never as authority."""
        if source is None:
            raise ProjectionLifecycleError(
                "PROMOTION_PROJECTION_SOURCE_REQUIRED",
                "Architecture promotion requires a trusted projection refresh source",
            )
        if source.project_id != plan_record.project_id:
            raise ProjectionLifecycleError(
                "PROJECT_MISMATCH",
                "projection source belongs to another project",
            )

        if source.plan_records is not None:
            matching_records = tuple(
                record for record in source.plan_records if record.plan_id == plan_record.plan_id
            )
            if matching_records and matching_records[0] != plan_record:
                raise ProjectionLifecycleError(
                    "PROMOTION_PLAN_BINDING_MISMATCH",
                    "projection source Plan record disagrees with the live Project Governance Store record",
                )
            if source.plan_records and not matching_records:
                raise ProjectionLifecycleError(
                    "PROMOTION_PLAN_BINDING_MISMATCH",
                    "projection source does not contain the promoted live Plan record",
                )

        matching_documents = tuple(
            document for document in source.plan_documents if document.plan_id == plan_record.plan_id
        )
        if len(matching_documents) > 1:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_BINDING_AMBIGUOUS",
                "projection source contains more than one document for the promoted Plan",
            )
        if matching_documents:
            plan_input = matching_documents[0]
            if plan_input.authority != plan_record.authority:
                raise ProjectionLifecycleError(
                    "PROMOTION_PLAN_AUTHORITY_MISMATCH",
                    "projection source Plan document is not bound to the live Plan authority",
                )
            if (
                plan_input.document.source_revision != trusted_plan.document.source_revision
                or plan_input.document.source_digest != trusted_plan.document.source_digest
            ):
                raise ProjectionLifecycleError(
                    "PROMOTION_PLAN_AUTHORITY_STALE",
                    "projection source Plan document is stale or forged",
                )
            try:
                candidate = architecture_state_from_plan_document(
                    project_id=plan_record.project_id,
                    plan_id=plan_record.plan_id,
                    document=plan_input.document,
                    authority_ref=plan_record.authority.authority_ref,
                )
            except GovernanceProjectionError as exc:
                raise ProjectionLifecycleError(exc.code, exc.message) from exc
            candidate_binding = (candidate.baseline_id, candidate.baseline_status)
            if candidate_binding != trusted_baseline:
                if candidate.baseline_status not in _ACCEPTED_BASELINE_STATUSES:
                    raise ProjectionLifecycleError(
                        "ARCHITECTURE_BASELINE_NOT_ACCEPTED",
                        f"baseline status {candidate.baseline_status!r} is not an accepted status",
                    )
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_MISMATCH",
                    "projection source Plan document disagrees with the trusted baseline",
                )

        architecture = source.architecture
        if architecture is not None:
            candidate_binding = (architecture.baseline_id, architecture.baseline_status)
            if (candidate_binding[0] is None) != (candidate_binding[1] is None):
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_BINDING_MISSING",
                    "projection source Architecture baseline identity and status must be complete",
                )
            if candidate_binding[0] is None:
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_BINDING_MISSING",
                    "projection source Architecture baseline identity and status are required",
                )
            if candidate_binding != trusted_baseline:
                if architecture.baseline_status not in _ACCEPTED_BASELINE_STATUSES:
                    raise ProjectionLifecycleError(
                        "ARCHITECTURE_BASELINE_NOT_ACCEPTED",
                        f"baseline status {architecture.baseline_status!r} is not an accepted status",
                    )
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_MISMATCH",
                    "projection source Architecture disagrees with the trusted baseline",
                )
            if architecture.accepted_ref is not None and architecture.accepted_ref != metadata.authority_ref:
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_AUTHORITY_REF_MISMATCH",
                    "projection source is bound to a different Architecture authority",
                )

    @staticmethod
    def _accepted_baseline_binding(
        *,
        project_id: str,
        plan_id: str | None,
        plan_records: tuple[Any, ...] | None,
        plan_documents: tuple[Any, ...],
        architecture: ArchitectureStateInput | None,
    ) -> tuple[str, str]:
        """Resolve the already-accepted baseline without trusting the request."""
        if plan_records is None:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_BINDING_MISSING",
                "Project Governance Store Plan records were not read",
            )
        records = tuple(plan_records)
        if plan_id is None:
            if len(records) != 1:
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_PLAN_AMBIGUOUS",
                    "accepted Architecture baseline is not bound to one Plan",
                )
            selected_plan_id = records[0].plan_id
        else:
            selected_plan_id = plan_id
        record = next((item for item in records if item.plan_id == selected_plan_id), None)
        if record is None:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_BINDING_MISSING",
                f"no accepted Project Governance Plan record for {selected_plan_id!r}",
            )
        if record.lifecycle_state != PLAN_LIFECYCLE_ACTIVE:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_NOT_ACCEPTED",
                "Architecture promotion requires an active accepted Plan record",
            )

        explicit: tuple[str | None, str | None] | None = None
        if architecture is not None:
            explicit = (architecture.baseline_id, architecture.baseline_status)
            if (explicit[0] is None) != (explicit[1] is None):
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_BINDING_MISSING",
                    "accepted Architecture baseline identity and status must be complete",
                )

        document_binding: tuple[str | None, str | None] | None = None
        matching_documents = tuple(
            document for document in plan_documents if document.plan_id == selected_plan_id
        )
        if len(matching_documents) > 1:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_BINDING_AMBIGUOUS",
                "more than one accepted Plan document was supplied for the baseline",
            )
        if matching_documents:
            plan_input = matching_documents[0]
            if plan_input.authority is None or plan_input.authority != record.authority:
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_AUTHORITY_MISMATCH",
                    "accepted Plan document is not bound to the Project Governance Store record",
                )
            try:
                derived = architecture_state_from_plan_document(
                    project_id=project_id,
                    plan_id=selected_plan_id,
                    document=plan_input.document,
                    authority_ref=record.authority.authority_ref,
                )
            except GovernanceProjectionError as exc:
                raise ProjectionLifecycleError(exc.code, exc.message) from exc
            document_binding = (derived.baseline_id, derived.baseline_status)
            if document_binding[0] is None or document_binding[1] is None:
                raise ProjectionLifecycleError(
                    "ARCHITECTURE_BASELINE_BINDING_MISSING",
                    "accepted Plan document has no complete Architecture baseline binding",
                )

        binding = explicit or document_binding
        if binding is None or binding[0] is None or binding[1] is None:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_BINDING_MISSING",
                "accepted Architecture baseline identity and status are required",
            )
        if document_binding is not None and binding != document_binding:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_MISMATCH",
                "supplied Architecture baseline disagrees with the accepted Plan document",
            )
        baseline_id, baseline_status = binding
        if baseline_status not in _ACCEPTED_BASELINE_STATUSES:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_NOT_ACCEPTED",
                f"baseline status {baseline_status!r} is not an accepted status",
            )
        return baseline_id, baseline_status

    def _architecture_state_from_authority(
        self,
        metadata: ArchitectureMetadataRecord,
        *,
        baseline_id: str,
        baseline_status: str,
    ) -> ArchitectureStateInput:
        if metadata.authority_ref != architecture_authority_reference(metadata.project_id):
            raise ProjectionLifecycleError(
                "ARCHITECTURE_AUTHORITY_REF_MISMATCH",
                "Architecture metadata is not bound to the canonical authority reference",
            )
        root = _trusted_root(self.governance_root, metadata.project_id)  # type: ignore[arg-type]
        content = self._verify_architecture_file(
            root / ARCHITECTURE_FILENAME,
            metadata.current_digest,
        )
        actual_digest = _digest_bytes(content)
        return ArchitectureStateInput(
            project_id=metadata.project_id,
            baseline_id=baseline_id,
            baseline_status=baseline_status,
            accepted_ref=metadata.authority_ref,
            plan_delta_ref=metadata.accepted_delta_ref,
            source_revision=str(metadata.revision),
            source_digest=actual_digest,
            current_version=metadata.current_version,
            current_digest=metadata.current_digest,
            promotion_receipt_ref=metadata.promotion_receipt_ref,
            promoted_by_plan_id=metadata.promoted_by_plan_id,
        )

    def _prepare_promoted_source(
        self,
        *,
        request: ArchitecturePromotionRequest,
        source: GovernanceProjectionInput | None,
        metadata: ArchitectureMetadataRecord,
        architecture_digest: str,
        context_route_input: ContextRouteInput | None = None,
    ) -> GovernanceProjectionInput:
        if source is None:
            raise ProjectionLifecycleError(
                "PROMOTION_PROJECTION_SOURCE_REQUIRED",
                "Architecture promotion requires a trusted projection refresh source",
            )
        if source.project_id != request.project_id:
            raise ProjectionLifecycleError(
                "PROJECT_MISMATCH",
                "projection source belongs to another project",
            )
        plan_record = self._promotion_plan_record(request.project_id, request.plan_id)
        baseline_id, baseline_status, trusted_plan = self._trusted_plan_baseline(plan_record)
        self._validate_promotion_source(
            source=source,
            plan_record=plan_record,
            trusted_baseline=(baseline_id, baseline_status),
            trusted_plan=trusted_plan,
            metadata=metadata,
        )
        if request.baseline_id != baseline_id:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_MISMATCH",
                "promotion baseline does not match the accepted baseline identity",
            )
        if request.baseline_status != baseline_status:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_STATUS_MISMATCH",
                "promotion baseline status does not match the accepted baseline status",
            )
        state = ArchitectureStateInput(
            project_id=request.project_id,
            baseline_id=baseline_id,
            baseline_status=baseline_status,
            accepted_ref=metadata.authority_ref,
            plan_delta_ref=metadata.accepted_delta_ref,
            source_revision=str(metadata.revision),
            source_digest=_require_digest(architecture_digest, "architecture_digest"),
            current_version=metadata.current_version,
            current_digest=metadata.current_digest,
            promotion_receipt_ref=metadata.promotion_receipt_ref,
            promoted_by_plan_id=metadata.promoted_by_plan_id,
        )
        refreshed = replace(source, architecture=state)
        try:
            refresh_request = ProjectionRefreshRequest(
                trigger=REFRESH_TRIGGER_ARCHITECTURE_PROMOTION,
                source=refreshed,
            )
        except GovernanceProjectionError as exc:
            raise ProjectionLifecycleError(exc.code, exc.message) from exc
        self._build_projection(
            refresh_request,
            context_route_input=context_route_input,
        )
        return refreshed

    def _validate_generated_view_destination(self, root: Path) -> None:
        if not root.is_dir() or root.is_symlink():
            raise ProjectionLifecycleError(
                "GENERATED_VIEW_DESTINATION_INVALID",
                "generated Architecture views require a trusted regular directory",
            )
        for name in ("MAP.md", "STATUS.md"):
            path = root / name
            if path.is_symlink():
                raise ProjectionLifecycleError(
                    "SYMLINK_REJECTED",
                    f"generated view destination {name} must not be a symlink",
                )
            if path.exists() and not path.is_file():
                raise ProjectionLifecycleError(
                    "GENERATED_VIEW_DESTINATION_INVALID",
                    f"generated view destination {name} must be a regular file",
                )

    def _preflight_promotion(
        self,
        *,
        request: ArchitecturePromotionRequest,
        source: GovernanceProjectionInput | None,
        current: ArchitectureMetadataRecord,
        root: Path,
        architecture_path: Path,
        context_route_input: ContextRouteInput | None = None,
    ) -> None:
        self._verify_architecture_file(architecture_path, request.expected_current_digest)
        if current.revision != request.expected_revision:
            raise ProjectionLifecycleError(
                "PROMOTION_CAS_FAILED",
                "Architecture metadata revision does not match the expected precondition",
            )
        if (
            current.current_version != request.expected_current_version
            or current.current_digest != request.expected_current_digest
        ):
            raise ProjectionLifecycleError(
                "PROMOTION_CAS_FAILED",
                "Architecture metadata digest/version does not match the expected precondition",
            )
        self._validate_generated_view_destination(root)
        target = ArchitectureMetadataRecord(
            project_id=current.project_id,
            current_version=request.target_version,
            current_digest=request.target_digest,
            accepted_delta_ref=request.accepted_delta_ref,
            accepted_delta_digest=request.accepted_delta_digest,
            promotion_receipt_ref=request.promotion_receipt_ref,
            revision=current.revision + 1,
            promoted_by_plan_id=request.plan_id,
        )
        self._prepare_promoted_source(
            request=request,
            source=source,
            metadata=target,
            architecture_digest=request.target_digest,
            context_route_input=context_route_input,
        )

    @staticmethod
    def _metadata_matches_target(
        metadata: ArchitectureMetadataRecord, request: ArchitecturePromotionRequest
    ) -> bool:
        return (
            metadata.current_version == request.target_version
            and metadata.current_digest == request.target_digest
            and metadata.accepted_delta_ref == request.accepted_delta_ref
            and metadata.accepted_delta_digest == request.accepted_delta_digest
            and metadata.promoted_by_plan_id == request.plan_id
        )

    @staticmethod
    def _metadata_matches_receipt(
        metadata: ArchitectureMetadataRecord, receipt: ArchitecturePromotionReceipt
    ) -> bool:
        resulting_revision = (
            receipt.resulting_revision
            if receipt.resulting_revision is not None
            else receipt.expected_revision + 1
        )
        return (
            metadata.current_version == receipt.target_version
            and metadata.current_digest == receipt.target_digest
            and metadata.accepted_delta_ref == receipt.accepted_delta_ref
            and metadata.accepted_delta_digest == receipt.accepted_delta_digest
            and metadata.promotion_receipt_ref == receipt.promotion_receipt_ref
            and metadata.promoted_by_plan_id == receipt.plan_id
            and metadata.revision == resulting_revision
        )

    @staticmethod
    def _verify_architecture_file(path: Path, expected_digest: str) -> bytes:
        content = _read_file(path, max_bytes=MAX_ARCHITECTURE_BYTES)
        if _digest_bytes(content) != expected_digest:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_DIGEST_MISMATCH",
                f"{path.name} does not match the expected Architecture digest",
            )
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectionLifecycleError("ARCHITECTURE_CONTENT_INVALID", "Architecture file must be UTF-8") from exc
        return content

    @staticmethod
    def _read_receipt(path: Path) -> ArchitecturePromotionReceipt | None:
        if not path.exists():
            return None
        try:
            raw = _read_file(path, max_bytes=64 * 1024)
            data = json.loads(raw.decode("utf-8"))
            return ArchitecturePromotionReceipt.from_dict(data)
        except ProjectionLifecycleError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ProjectionLifecycleError("RECEIPT_INVALID", f"cannot read promotion receipt: {exc}") from exc

    @staticmethod
    def _write_receipt(path: Path, receipt: ArchitecturePromotionReceipt) -> None:
        payload = json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        _atomic_write(path, payload.encode("utf-8"))
        if _read_file(path, max_bytes=len(payload.encode("utf-8"))) != payload.encode("utf-8"):
            raise ProjectionLifecycleError("RECEIPT_READ_BACK_MISMATCH", "promotion receipt read-back did not match")

    @staticmethod
    def _require_committed_receipt(
        receipt: ArchitecturePromotionReceipt | None,
        request: ArchitecturePromotionRequest,
    ) -> ArchitecturePromotionReceipt:
        if receipt is None or receipt.phase != "committed" or receipt.receipt_id != _receipt_id(request):
            raise ProjectionLifecycleError(
                "PROMOTION_RECEIPT_MISSING", "already-promoted metadata has no matching committed receipt"
            )
        return receipt

    def _refresh_promoted_source(
        self,
        source: GovernanceProjectionInput | None,
        request: ArchitecturePromotionRequest,
        metadata: ArchitectureMetadataRecord,
        *,
        context_route_input: ContextRouteInput | None = None,
    ) -> ProjectionRefreshResult | None:
        refreshed = self._prepare_promoted_source(
            request=request,
            source=source,
            metadata=metadata,
            architecture_digest=metadata.current_digest,
            context_route_input=context_route_input,
        )
        return self.refresh_for_trigger(
            REFRESH_TRIGGER_ARCHITECTURE_PROMOTION,
            refreshed,
            project_directory=self.governance_root.root_path,  # type: ignore[union-attr]
            context_route_input=context_route_input,
        )

    def _refresh_receipt_source(
        self,
        source: GovernanceProjectionInput | None,
        receipt: ArchitecturePromotionReceipt,
        metadata: ArchitectureMetadataRecord,
        *,
        context_route_input: ContextRouteInput | None = None,
    ) -> ProjectionRefreshResult | None:
        if source is None:
            raise ProjectionLifecycleError(
                "PROMOTION_PROJECTION_SOURCE_REQUIRED",
                "Architecture recovery requires a trusted projection refresh source",
            )
        if source.project_id != receipt.project_id:
            raise ProjectionLifecycleError("PROJECT_MISMATCH", "projection source belongs to another project")
        plan_record = self._promotion_plan_record(receipt.project_id, receipt.plan_id)
        baseline_id, baseline_status, trusted_plan = self._trusted_plan_baseline(plan_record)
        self._validate_promotion_source(
            source=source,
            plan_record=plan_record,
            trusted_baseline=(baseline_id, baseline_status),
            trusted_plan=trusted_plan,
            metadata=metadata,
        )
        if baseline_id != receipt.baseline_id or baseline_status != receipt.baseline_status:
            raise ProjectionLifecycleError(
                "ARCHITECTURE_BASELINE_MISMATCH",
                "promotion receipt does not match the accepted baseline binding",
            )
        architecture = self._architecture_state_from_authority(
            metadata,
            baseline_id=baseline_id,
            baseline_status=baseline_status,
        )
        return self.refresh_for_trigger(
            REFRESH_TRIGGER_ARCHITECTURE_PROMOTION,
            replace(source, architecture=architecture),
            project_directory=self.governance_root.root_path,  # type: ignore[union-attr]
            context_route_input=context_route_input,
        )

    @staticmethod
    def _remove_stage(path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        try:
            path.unlink()
        except OSError as exc:
            raise ProjectionLifecycleError(
                "PROMOTION_STAGE_CLEANUP_FAILED",
                f"cannot remove staged Architecture file: {exc}",
            ) from exc


__all__ = [
    "ARCHITECTURE_CARD_REFRESH_AFTER_PROMOTION",
    "ARCHITECTURE_CONTENT_DUPLICATED_IN_DB",
    "ARCHITECTURE_CAS_REQUIRED",
    "ARCHITECTURE_ACCEPTED_BASELINE_BINDING",
    "ARCHITECTURE_DB_METADATA_IS_SEMANTIC_AUTHORITY",
    "ARCHITECTURE_FILENAME",
    "ARCHITECTURE_MD_IS_SEMANTIC_AUTHORITY",
    "ARCHITECTURE_INVALID_PROMOTION_FAILS_CLOSED",
    "ARCHITECTURE_PROJECTION_REQUIRES_REAL_AUTHORITY_FILE",
    "ARCHITECTURE_PROMOTION_IMPLEMENTED",
    "ARCHITECTURE_RECEIPT_FILENAME",
    "ARCHITECTURE_STAGE_FILENAME",
    "BOUNDED_LIFECYCLE_TRIGGERS",
    "CALLER_AUTHORITY_OVERRIDE_COUNT",
    "CALLER_PROJECTION_SOURCE_IS_AUTHORITY",
    "CONTEXT_ROUTE_REFRESH_AFTER_PROMOTION",
    "EVENT_BUS_CREATED",
    "FINAL_PROMOTION_PRECONDITIONS_FROM_TRUSTED_STATE",
    "GENERIC_WATCHER_CREATED",
    "MAP_REFRESH_AFTER_PROMOTION",
    "MAX_ARCHITECTURE_BYTES",
    "PGS_PLAN_STATE_RELOADED_DURING_PROMOTION",
    "PROCESS_LOCAL_PROGRESS_AUTHORITY",
    "PROMOTION_WITHOUT_PROJECTION_SOURCE_DENIED",
    "PROJECTION_LIFECYCLE_IMPLEMENTED",
    "PROJECTION_REFRESH_IS_EXPLICIT",
    "PROGRESS_CARD_IS_DERIVED",
    "PROGRESS_LIFECYCLE_HOOKS_WIRED",
    "PROMOTION_RECEIPT_DURABLE",
    "PROMOTION_RESTART_RECOVERY",
    "PROGRESS_REFRESH_DEPENDS_ON_STEWARD",
    "RESTART_PROGRESS_TRUTH_EQUIVALENT",
    "SECOND_PROJECTION_ENGINE_CREATED",
    "SECOND_ARCHITECTURE_STORE_CREATED",
    "SECOND_PROGRESS_STORE_CREATED",
    "STATE_OWNERSHIP_PRESERVED",
    "STATUS_REFRESH_AFTER_PROMOTION",
    "STATUS_IS_DERIVED",
    "VERIFY_AFTER_WRITE",
    "ArchitecturePromotionReceipt",
    "ArchitecturePromotionRequest",
    "ArchitecturePromotionResult",
    "GovernanceProjectionLifecycle",
    "ProjectionLifecycleError",
    "ProjectionRefreshReceipt",
    "ProjectionRefreshResult",
]
