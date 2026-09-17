"""AF #57 M1/W2 — Local Governance Plan authority adapter (Go 2.0 local path).

Implements the smallest local Governance 2.0 Plan authority behind the
existing executor-neutral ``PlanAuthorityMutationPort`` (mutation) and the
existing ``PlanAuthorityReadAdapter`` boundary (read).  It creates no second
Plan mutation protocol, no second Plan ontology and no generic governance
write API; the durable journal (``DurableJournalStore``) and the
``RecoveryExecutor`` remain the one mutation pipeline.

Trusted destination binding:

    LocalGovernanceRootBinding (trusted operator governance base + canonical
    project id -> project-scoped root)
  + canonical internal plan_id
  + expected Plan subject ref
      -> LocalPlanAuthorityDestination

The physical destination is resolved exclusively from that trusted binding:
an ``ObjectRef`` alone never derives a filesystem destination, and a wrong
target fails closed before any filesystem effect.

Read pipeline (existing semantic pipeline, bounded local source kind only):

    plan.md
      -> LocalPlanAuthorityReadAdapter (PlanAuthoritySnapshot)
      -> normalize_portable_plan(..., source_kind=portable_plan_local)
      -> PortablePlanDocument

Invariants:

    LOCAL_PLAN_AUTHORITY_ADAPTER_IMPLEMENTED=yes
    LOCAL_PLAN_AUTHORITY_READ_ADAPTER_IMPLEMENTED=yes
    PLAN_AUTHORITY_MUTATION_PORT_REUSED=yes
    SECOND_PLAN_MUTATION_PROTOCOL_CREATED=no
    SECOND_PLAN_ONTOLOGY_CREATED=no
    DURABLE_JOURNAL_REUSED=yes
    RECOVERY_EXECUTOR_REUSED=yes
    ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED=no
    OBJECT_REF_DERIVES_FILESYSTEM_DESTINATION=no
    LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY=no
    LOCAL_PLAN_RETIREMENT_IMPLEMENTED=no (no M1 consumer; the adapter
    reports a truthful typed known no-effect instead of inventing semantics)
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from aota_forge.adapters.plan_authority import (
    PlanAuthorityReadAdapter,
    PlanAuthoritySnapshot,
)
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
    PlanAuthorityBindingError,
    require_bound_authority,
)
from aota_forge.adapters.plan_authority.port import (
    PlanAuthorityMutationPort,
    PortablePlanMutationRequest,
    PortablePlanMutationResponse,
)
from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
)
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import (
    MAX_PLAN_BYTES,
    PORTABLE_PLAN_SOURCE_LOCAL,
    PortablePlanDocument,
)
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding

PLAN_DOCUMENT_FILENAME = "plan.md"
PLAN_AUTHORITY_REF_PREFIX = "local-governance"
AUTHORITY_REF_SEPARATOR = ":"
PLAN_AUTHORITY_REF_SEPARATOR = "/"

# The canonical raw digest of an absent (or empty) local Plan document. A
# non-empty existing document never equals it, so "no authoritative content
# yet" is represented without inventing a second protocol or digest domain.
ABSENT_RAW_DOCUMENT_DIGEST = hashlib.sha256(b"").hexdigest()

LOCAL_PLAN_AUTHORITY_ADAPTER_IMPLEMENTED = True
LOCAL_PLAN_AUTHORITY_READ_ADAPTER_IMPLEMENTED = True
PLAN_AUTHORITY_MUTATION_PORT_REUSED = True
SECOND_PLAN_MUTATION_PROTOCOL_CREATED = False
SECOND_PLAN_ONTOLOGY_CREATED = False
DURABLE_JOURNAL_REUSED = True
RECOVERY_EXECUTOR_REUSED = True
ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED = False
OBJECT_REF_DERIVES_FILESYSTEM_DESTINATION = False
LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY = False
LOCAL_PLAN_RETIREMENT_IMPLEMENTED = False


class LocalGovernanceAdapterError(ValueError):
    """Fail-closed local governance Plan authority adapter error."""


class LocalGovernanceAuthorityError(LocalGovernanceAdapterError):
    """The request target does not match the trusted destination binding."""


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def local_plan_authority_reference(project_id: str, plan_id: str) -> str:
    """Source-neutral local Plan authority reference (never a host path)."""
    if not isinstance(project_id, str) or not project_id.strip():
        raise LocalGovernanceAdapterError("project_id must be a non-empty string")
    if not is_plan_id(plan_id):
        raise LocalGovernanceAdapterError("plan_id must be one canonical internal Plan ID")
    return (
        f"{PLAN_AUTHORITY_REF_PREFIX}{AUTHORITY_REF_SEPARATOR}"
        f"{project_id.strip()}{PLAN_AUTHORITY_REF_SEPARATOR}{plan_id}"
    )


@dataclass(frozen=True)
class LocalPlanAuthorityDestination:
    """Trusted local Plan destination binding.

    Sufficient to resolve exactly one physical Plan document:

    * ``governance_root`` — the trusted project-scoped governance root
    * ``plan_id`` — the canonical internal Plan identity
    * ``expected_ref`` — the Plan subject ref this authority serves

    The destination is constructed by trusted composition only; it accepts no
    model-supplied path and grants no authority by itself.
    """

    governance_root: LocalGovernanceRootBinding
    plan_id: str
    expected_ref: ObjectRef

    def __post_init__(self) -> None:
        if not isinstance(self.governance_root, LocalGovernanceRootBinding):
            raise LocalGovernanceAdapterError(
                "destination requires a trusted LocalGovernanceRootBinding"
            )
        if not is_plan_id(self.plan_id):
            raise LocalGovernanceAdapterError(
                "destination plan_id must be one canonical internal Plan ID"
            )
        if not isinstance(self.expected_ref, ObjectRef):
            raise LocalGovernanceAdapterError("destination expected_ref must be an ObjectRef")
        if self.expected_ref.object_kind != IdKind.SUBJECT:
            raise LocalGovernanceAdapterError("destination expected_ref must be a subject ref")
        if self.expected_ref.internal_id.sub_kind != SubjectKind.PLAN:
            raise LocalGovernanceAdapterError("destination expected_ref must be a Plan subject ref")
        if self.expected_ref.internal_id.value != self.plan_id:
            raise LocalGovernanceAdapterError(
                "destination expected_ref must identify the destination plan_id"
            )

    @property
    def project_id(self) -> str:
        return self.governance_root.project_id

    @property
    def authority_ref(self) -> str:
        return local_plan_authority_reference(self.project_id, self.plan_id)

    def plan_directory(self) -> Path:
        return Path(self.governance_root.root_path) / self.plan_id

    def plan_document_path(self) -> Path:
        return self.plan_directory() / PLAN_DOCUMENT_FILENAME

    def contains_target(self, target: object) -> bool:
        try:
            return (
                isinstance(target, ObjectRef)
                and target.to_canonical() == self.expected_ref.to_canonical()
            )
        except Exception:
            return False

    def require_target(self, target: object) -> None:
        if not self.contains_target(target):
            raise LocalGovernanceAuthorityError(
                "typed_target does not match the trusted destination Plan binding"
            )


def _read_raw_document(destination: LocalPlanAuthorityDestination) -> tuple[str | None, str, str]:
    """Read the bounded raw local Plan document.

    Returns ``(source_revision, raw_digest, raw_body)``; an absent document is
    revision ``None`` with the canonical absent/empty raw digest and an empty
    body. Never follows a symlinked document or directory.
    """
    document = destination.plan_document_path()
    if document.is_symlink():
        raise LocalGovernanceAdapterError("local Plan document must not be a symlink")
    if not document.exists():
        return (None, ABSENT_RAW_DOCUMENT_DIGEST, "")
    if not document.is_file():
        raise LocalGovernanceAdapterError("local Plan document path is not a regular file")
    try:
        size = document.stat().st_size
    except OSError as exc:
        raise LocalGovernanceAdapterError(f"local Plan document unreadable: {exc}") from exc
    if size > MAX_PLAN_BYTES:
        raise LocalGovernanceAdapterError("local Plan document exceeds the bounded Plan size")
    try:
        with document.open("rb") as handle:
            raw_body = handle.read(MAX_PLAN_BYTES + 1)
    except OSError as exc:
        raise LocalGovernanceAdapterError(f"local Plan document unreadable: {exc}") from exc
    if len(raw_body) > MAX_PLAN_BYTES:
        raise LocalGovernanceAdapterError("local Plan document exceeds the bounded Plan size")
    try:
        body = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise LocalGovernanceAdapterError("local Plan document must be valid UTF-8") from exc
    digest = _digest(body)
    return (digest, digest, body)


def _rejection(
    request: PortablePlanMutationRequest, error_code: str, message: str
) -> PortablePlanMutationResponse:
    return PortablePlanMutationResponse(
        operation=request.operation,
        typed_target=request.typed_target.to_canonical(),
        correlation_id=request.correlation_id,
        adapter_success=False,
        error_code=error_code,
        error_message=message,
    )


def _stale(
    request: PortablePlanMutationRequest,
    observed_revision: str | None,
    observed_digest: str,
) -> PortablePlanMutationResponse:
    return PortablePlanMutationResponse(
        operation=request.operation,
        typed_target=request.typed_target.to_canonical(),
        correlation_id=request.correlation_id,
        adapter_success=False,
        observed_raw_digest=observed_digest,
        observed_revision=observed_revision,
        error_code="STALE_AUTHORITY",
        error_message="observed local authority does not match the authorized precondition",
    )


def _require_local_plan_binding(
    binding: PlanAuthorityBinding | None, destination: LocalPlanAuthorityDestination
) -> PlanAuthorityBinding:
    try:
        bound = require_bound_authority(binding)
    except PlanAuthorityBindingError as exc:
        raise LocalGovernanceAdapterError(
            f"local Plan read requires one explicitly bound local authority: {exc}"
        ) from exc
    if bound.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        raise LocalGovernanceAdapterError(
            "local Plan read requires a local_governance authority binding"
        )
    if bound.plan_id != destination.plan_id:
        raise LocalGovernanceAdapterError(
            "bound authority plan_id does not match the trusted destination Plan identity"
        )
    if bound.authority_ref != destination.authority_ref:
        raise LocalGovernanceAdapterError(
            "bound authority reference does not match the trusted destination authority reference"
        )
    return bound


class LocalPlanAuthorityReadAdapter(PlanAuthorityReadAdapter):
    """Read-only local Plan authority adapter (plan.md -> snapshot).

    The adapter exposes a bounded source-neutral ``plan_authority`` reference
    for existing projection/composition consumers; the physical host path is
    never exposed as semantic authority.
    """

    def __init__(
        self,
        destination: LocalPlanAuthorityDestination,
        *,
        binding: PlanAuthorityBinding | None,
    ) -> None:
        if not isinstance(destination, LocalPlanAuthorityDestination):
            raise LocalGovernanceAdapterError(
                "read adapter requires a LocalPlanAuthorityDestination"
            )
        self._destination = destination
        self._binding = _require_local_plan_binding(binding, destination)

    @property
    def destination(self) -> LocalPlanAuthorityDestination:
        return self._destination

    @property
    def plan_authority(self) -> str:
        return self._binding.authority_ref

    def load(self) -> PlanAuthoritySnapshot:
        revision, digest, body = _read_raw_document(self._destination)
        return PlanAuthoritySnapshot(
            body=body,
            revision=revision,
            digest=digest,
            control_projections={},
        )


def load_local_portable_plan(adapter: LocalPlanAuthorityReadAdapter) -> PortablePlanDocument:
    """Existing semantic pipeline over the local Plan authority.

    ``plan.md -> snapshot -> normalize_portable_plan(portable_plan_local)``.
    No second Plan ontology is created; the normalized document is the same
    PortablePlanDocument used for Governance 1.x issue bodies.
    """
    if not isinstance(adapter, LocalPlanAuthorityReadAdapter):
        raise LocalGovernanceAdapterError("expected a LocalPlanAuthorityReadAdapter")
    snapshot = adapter.load()
    return normalize_portable_plan(
        snapshot.body,
        source_revision=snapshot.revision,
        source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
    )


class LocalPlanAuthorityAdapter(PlanAuthorityMutationPort):
    """Local Governance Plan mutation adapter behind PlanAuthorityMutationPort.

    Read-before-write, at-most-one attempt, verify-after-write, unknown
    outcome and fresh retry authorization stay owned by the existing durable
    journal + RecoveryExecutor pipeline. This adapter only performs exactly
    one bounded physical materialization attempt per ``mutate`` call and never
    treats adapter success as verification.
    """

    def __init__(self, destination: LocalPlanAuthorityDestination) -> None:
        if not isinstance(destination, LocalPlanAuthorityDestination):
            raise LocalGovernanceAdapterError(
                "mutation adapter requires a LocalPlanAuthorityDestination"
            )
        self._destination = destination
        self.read_count = 0
        self.verify_count = 0
        self.mutate_count = 0
        self.write_count = 0

    @property
    def destination(self) -> LocalPlanAuthorityDestination:
        return self._destination

    def read_raw_authority(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self._destination.require_target(target)
        self.read_count += 1
        return _read_raw_document(self._destination)

    def verify(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self._destination.require_target(target)
        self.verify_count += 1
        return _read_raw_document(self._destination)

    def mutate(self, request: PortablePlanMutationRequest) -> PortablePlanMutationResponse:
        self.mutate_count += 1
        if not isinstance(request, PortablePlanMutationRequest):
            raise LocalGovernanceAdapterError("mutate requires a PortablePlanMutationRequest")
        if not self._destination.contains_target(request.typed_target):
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "typed_target does not match the trusted destination Plan binding; no effect",
            )
        if request.operation == PLAN_RETIREMENT_OPERATION:
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "plan_retirement is not implemented for local governance in M1 "
                "(truthful known no-effect, no invented semantics)",
            )
        if request.operation != PLAN_INIT_OPERATION:
            return _rejection(
                request,
                "KNOWN_REJECTION",
                f"operation {request.operation!r} is not supported by the local Plan authority adapter",
            )
        body = request.candidate_raw_body
        if not isinstance(body, str) or not body:
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "plan_init requires candidate_raw_body for local materialization",
            )
        try:
            encoded_body = body.encode("utf-8")
        except UnicodeEncodeError:
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "candidate_raw_body must be valid UTF-8 text",
            )
        if len(encoded_body) > MAX_PLAN_BYTES:
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "candidate_raw_body exceeds the bounded Plan size",
            )
        body_digest = hashlib.sha256(encoded_body).hexdigest()
        if request.candidate_raw_digest is not None and request.candidate_raw_digest != body_digest:
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "candidate body digest does not match the authorized candidate digest",
            )
        current_revision, current_digest, current_body = _read_raw_document(self._destination)
        if (
            request.authority_source_revision is not None
            and request.authority_source_revision != current_revision
        ):
            return _stale(request, current_revision, current_digest)
        if (
            request.authority_observed_raw_digest is not None
            and request.authority_observed_raw_digest != current_digest
        ):
            return _stale(request, current_revision, current_digest)
        if current_body:
            if current_body == body:
                return PortablePlanMutationResponse(
                    operation=request.operation,
                    typed_target=request.typed_target.to_canonical(),
                    correlation_id=request.correlation_id,
                    adapter_success=True,
                    observed_raw_digest=current_digest,
                    observed_revision=current_revision,
                )
            return _rejection(
                request,
                "KNOWN_REJECTION",
                "plan_init does not amend an existing non-empty local Plan document; no effect",
            )
        revision, digest = self._materialize(body)
        self.write_count += 1
        return PortablePlanMutationResponse(
            operation=request.operation,
            typed_target=request.typed_target.to_canonical(),
            correlation_id=request.correlation_id,
            adapter_success=True,
            observed_raw_digest=digest,
            observed_revision=revision,
        )

    def _materialize(self, body: str) -> tuple[str, str]:
        destination = self._destination
        destination.governance_root.revalidate()
        root = Path(destination.governance_root.root_path)
        plan_directory = destination.plan_directory()
        if plan_directory.parent != root or plan_directory.name != destination.plan_id:
            raise LocalGovernanceAdapterError(
                "Plan directory must be a direct project-scoped child of the bound governance root"
            )
        if plan_directory.is_symlink():
            raise LocalGovernanceAdapterError("Plan directory must not be a symlink")
        if not plan_directory.exists():
            plan_directory.mkdir(mode=0o755)
        elif not plan_directory.is_dir():
            raise LocalGovernanceAdapterError("Plan path exists and is not a directory")
        document = destination.plan_document_path()
        if document.is_symlink():
            raise LocalGovernanceAdapterError("local Plan document must not be a symlink")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".plan-document-", suffix=".tmp", dir=str(plan_directory)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, document)
            temporary_name = ""
        finally:
            if temporary_name and os.path.exists(temporary_name):
                os.unlink(temporary_name)
        digest = _digest(body)
        return (digest, digest)


__all__ = [
    "PLAN_DOCUMENT_FILENAME",
    "ABSENT_RAW_DOCUMENT_DIGEST",
    "LOCAL_PLAN_AUTHORITY_ADAPTER_IMPLEMENTED",
    "LOCAL_PLAN_AUTHORITY_READ_ADAPTER_IMPLEMENTED",
    "PLAN_AUTHORITY_MUTATION_PORT_REUSED",
    "SECOND_PLAN_MUTATION_PROTOCOL_CREATED",
    "SECOND_PLAN_ONTOLOGY_CREATED",
    "DURABLE_JOURNAL_REUSED",
    "RECOVERY_EXECUTOR_REUSED",
    "ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED",
    "OBJECT_REF_DERIVES_FILESYSTEM_DESTINATION",
    "LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY",
    "LOCAL_PLAN_RETIREMENT_IMPLEMENTED",
    "LocalGovernanceAdapterError",
    "LocalGovernanceAuthorityError",
    "local_plan_authority_reference",
    "LocalPlanAuthorityDestination",
    "LocalPlanAuthorityReadAdapter",
    "LocalPlanAuthorityAdapter",
    "load_local_portable_plan",
]
