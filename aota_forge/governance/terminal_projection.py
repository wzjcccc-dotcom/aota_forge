"""Bounded terminal Local Plan projection reconciliation.

This Governance-domain effect re-reads terminal authority from its durable
owners, then delegates deterministic derived output to the existing
``GovernanceProjectionLifecycle``. It does not advance workflow or mutate any
authority owner.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    local_plan_authority_reference,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, object_ref_subject
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import load_project
from aota_forge.governance.context_route import (
    ROUTE_ENTRY_PLAN_AUTHORITY,
    ROUTE_ENTRY_PLAN_CARD,
    ContextRouteInput,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_RETIRED,
    ProjectGovernanceStore,
    ProjectPlanRecord,
)
from aota_forge.governance.projection import (
    REFRESH_TRIGGER_PLAN_MUTATION,
    GovernanceProjectionInput,
    plan_document_input,
)
from aota_forge.governance.projection_lifecycle import (
    GovernanceProjectionLifecycle,
    ProjectionLifecycleError,
    ProjectionRefreshResult,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding


class TerminalProjectionReconciliationError(ValueError):
    """Typed fail-closed error for terminal-only projection reconciliation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class TerminalProjectionReconciliationResult:
    """Bounded verified outcome; it contains no physical filesystem paths."""

    project_id: str
    plan_id: str
    projection_digest: str
    status: str = "VERIFIED"
    authority_mutation: str = "NONE"
    terminal_authority: str = "VERIFIED"
    projection_convergence: str = "VERIFIED"

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "plan_id": self.plan_id,
            "authority_mutation": self.authority_mutation,
            "terminal_authority": self.terminal_authority,
            "projection_convergence": self.projection_convergence,
            "projection_digest": self.projection_digest,
        }


def _expected_plan_ref(plan_id: str) -> ObjectRef:
    return object_ref_subject(
        make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN)
    )


@contextmanager
def _governance_store(
    value: ProjectGovernanceStore | str | Path,
) -> Iterator[ProjectGovernanceStore]:
    if isinstance(value, ProjectGovernanceStore):
        yield value
        return
    if isinstance(value, (str, Path)):
        store = SQLiteProjectGovernanceStore(value)
        try:
            yield store
        finally:
            store.close()
        return
    raise TypeError("governance_store must be a ProjectGovernanceStore or storage path")


def _read_local_plan(
    *,
    root: LocalGovernanceRootBinding,
    record: ProjectPlanRecord,
) -> tuple[Any, Any]:
    authority = record.authority
    if (
        authority.plan_id != record.plan_id
        or authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        or authority.authority_ref
        != local_plan_authority_reference(record.project_id, record.plan_id)
        or authority.source_revision is None
        or authority.source_digest is None
    ):
        raise TerminalProjectionReconciliationError(
            "CURRENT_AUTHORITY_MISMATCH",
            "current Store record does not contain a complete Local Governance authority binding",
        )
    reader = LocalPlanAuthorityReadAdapter(
        LocalPlanAuthorityDestination(
            governance_root=root,
            plan_id=record.plan_id,
            expected_ref=_expected_plan_ref(record.plan_id),
        ),
        binding=authority,
    )
    try:
        snapshot = reader.load()
        document = normalize_portable_plan(
            snapshot.body,
            source_revision=snapshot.revision,
            source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
        )
    except Exception as exc:
        raise TerminalProjectionReconciliationError(
            "CURRENT_AUTHORITY_MISMATCH",
            "current Local Plan authority could not be read and normalized",
        ) from exc
    if (
        str(snapshot.revision) != str(authority.source_revision)
        or snapshot.digest != authority.source_digest
    ):
        raise TerminalProjectionReconciliationError(
            "CURRENT_AUTHORITY_MISMATCH",
            "current Store binding does not match the current Local Plan revision and digest",
        )
    return snapshot, document


def create_terminal_projection_reconciler(
    *,
    project_id: str,
    plan_id: str,
    project_root: str | Path,
    governance_root: LocalGovernanceRootBinding,
    governance_store: ProjectGovernanceStore | str | Path,
    coordinator_store: Any,
    execution_store: Any,
) -> Callable[[], TerminalProjectionReconciliationResult]:
    """Create a server-bound no-argument capability for one Plan selector.

    ``plan_id`` selects the Plan identity only. Every invocation obtains the
    current authority binding from the Governance Store and verifies it
    against the current Local Plan body before writing generated views.
    """
    if not isinstance(project_id, str) or not project_id.strip():
        raise TerminalProjectionReconciliationError(
            "TRUSTED_PROJECTION_CONTEXT_REQUIRED", "trusted project identity is required"
        )
    if not is_plan_id(plan_id):
        raise TerminalProjectionReconciliationError(
            "TRUSTED_PROJECTION_CONTEXT_REQUIRED", "trusted Plan identity is required"
        )
    if not isinstance(governance_root, LocalGovernanceRootBinding):
        raise TerminalProjectionReconciliationError(
            "TRUSTED_PROJECTION_CONTEXT_REQUIRED", "trusted Governance root is required"
        )
    if governance_root.project_id != project_id:
        raise TerminalProjectionReconciliationError(
            "PROJECT_MISMATCH", "trusted Governance root belongs to another Project"
        )
    if coordinator_store is None or execution_store is None:
        raise TerminalProjectionReconciliationError(
            "TRUSTED_PROJECTION_CONTEXT_REQUIRED",
            "trusted coordinator and execution owners are required",
        )
    project_root_path = Path(project_root)
    project_directory = Path(governance_root.root_path)

    def reconcile() -> TerminalProjectionReconciliationResult:
        try:
            governance_root.revalidate()
            if project_directory.is_symlink() or not project_directory.is_dir():
                raise TerminalProjectionReconciliationError(
                    "TRUSTED_PROJECTION_CONTEXT_REQUIRED",
                    "trusted Governance projection destination is unavailable",
                )
            if (
                project_root_path.is_symlink()
                or project_root_path.resolve(strict=True) != project_root_path
            ):
                raise TerminalProjectionReconciliationError(
                    "PROJECT_MISMATCH", "trusted Project root is not canonical"
                )
            manifest_path = project_root_path / ".aota" / "project.yaml"
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise TerminalProjectionReconciliationError(
                    "PROJECT_MISMATCH", "trusted Project manifest is unavailable"
                )
            manifest_root, manifest = load_project(manifest_path)
            if manifest_root != project_root_path or manifest["project"]["id"] != project_id:
                raise TerminalProjectionReconciliationError(
                    "PROJECT_MISMATCH", "trusted Project identity does not match its manifest"
                )

            with _governance_store(governance_store) as store:
                records = tuple(store.list_plans(project_id))
                record_by_id: dict[str, ProjectPlanRecord] = {}
                for record in records:
                    if record.project_id != project_id or record.plan_id in record_by_id:
                        raise TerminalProjectionReconciliationError(
                            "CURRENT_AUTHORITY_MISMATCH",
                            "current Governance Store contains conflicting Plan identities",
                        )
                    record_by_id[record.plan_id] = record

                terminal_record = record_by_id.get(plan_id)
                if terminal_record is None:
                    raise TerminalProjectionReconciliationError(
                        "TERMINAL_PRECONDITION_FAILED",
                        "current Governance Store does not contain the trusted Plan identity",
                    )
                if terminal_record.lifecycle_state != PLAN_LIFECYCLE_RETIRED:
                    raise TerminalProjectionReconciliationError(
                        "TERMINAL_PRECONDITION_FAILED",
                        "terminal projection reconciliation requires a retired Plan",
                    )
                if terminal_record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
                    raise TerminalProjectionReconciliationError(
                        "TERMINAL_PRECONDITION_FAILED",
                        "terminal projection reconciliation requires Local Governance authority",
                    )

                documents = []
                terminal_document = None
                for record in records:
                    if record.authority.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
                        continue
                    _snapshot, document = _read_local_plan(
                        root=governance_root,
                        record=record,
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
                    if record.lifecycle_state == PLAN_LIFECYCLE_RETIRED and (
                        document.plan_status != "completed"
                    ):
                        raise TerminalProjectionReconciliationError(
                            (
                                "TERMINAL_PRECONDITION_FAILED"
                                if record.plan_id == plan_id
                                else "CURRENT_AUTHORITY_MISMATCH"
                            ),
                            "a retired Local Plan does not have a completed authority body",
                        )
                    if record.plan_id == plan_id:
                        terminal_document = document

                if terminal_document is None or terminal_document.plan_status != "completed":
                    raise TerminalProjectionReconciliationError(
                        "TERMINAL_PRECONDITION_FAILED",
                        "current terminal Plan authority body is not completed",
                    )
                if store.get_plan(project_id, plan_id) != terminal_record:
                    raise TerminalProjectionReconciliationError(
                        "CURRENT_AUTHORITY_MISMATCH",
                        "terminal Governance Store record changed during verification",
                    )

                try:
                    coordinator_states = tuple(
                        state
                        for state in coordinator_store.list_all()
                        if state.project_id == project_id
                    )
                    execution_records = tuple(execution_store.list_all())
                except Exception as exc:
                    raise TerminalProjectionReconciliationError(
                        "DURABLE_OWNER_READ_FAILED",
                        "coordinator or execution projection owner could not be read",
                    ) from exc

                route = ContextRouteInput(
                    project_id=project_id,
                    plan_id=plan_id,
                    authority_source_kind=terminal_record.authority.source_kind,
                    authority_ref=terminal_record.authority.authority_ref,
                    authority_revision=str(terminal_record.authority.source_revision),
                    authority_digest=terminal_record.authority.source_digest,
                    authorized_root_refs=(("local-governance", ("read", "search")),),
                )
                source = GovernanceProjectionInput(
                    project_id=project_id,
                    project_ref=f"project:{project_id}",
                    project_status=manifest["project"]["status"],
                    governance_root_refs=("local-governance",),
                    plan_records=records,
                    plan_documents=tuple(documents),
                    coordinator_states=coordinator_states,
                    execution_records=execution_records,
                )
                try:
                    refreshed = GovernanceProjectionLifecycle(
                        governance_store=store,
                        governance_root=governance_root,
                        coordinator_store=coordinator_store,
                        execution_store=execution_store,
                    ).refresh_for_trigger(
                        REFRESH_TRIGGER_PLAN_MUTATION,
                        source,
                        project_directory=project_directory,
                        context_route_input=route,
                    )
                except Exception as exc:
                    raise TerminalProjectionReconciliationError(
                        "GOVERNANCE_PROJECTION_FAILED",
                        "Governance projection lifecycle did not converge",
                    ) from exc

                _verify_projection_readback(
                    refreshed=refreshed,
                    project_id=project_id,
                    plan_id=plan_id,
                    record=terminal_record,
                    document=terminal_document,
                    project_directory=project_directory,
                )
                if store.get_plan(project_id, plan_id) != terminal_record:
                    raise TerminalProjectionReconciliationError(
                        "CURRENT_AUTHORITY_MISMATCH",
                        "terminal Governance Store record changed during projection readback",
                    )
                _snapshot_after, document_after = _read_local_plan(
                    root=governance_root,
                    record=terminal_record,
                )
                if document_after.plan_status != "completed":
                    raise TerminalProjectionReconciliationError(
                        "CURRENT_AUTHORITY_MISMATCH",
                        "terminal Local Plan authority changed during projection readback",
                    )

                return TerminalProjectionReconciliationResult(
                    project_id=project_id,
                    plan_id=plan_id,
                    projection_digest=refreshed.bundle.projection_digest(),
                )
        except TerminalProjectionReconciliationError:
            raise
        except ProjectionLifecycleError as exc:
            raise TerminalProjectionReconciliationError(
                "GOVERNANCE_PROJECTION_FAILED",
                "Governance projection lifecycle did not converge",
            ) from exc
        except Exception as exc:
            code = getattr(exc, "code", None)
            if isinstance(code, str) and code.startswith("PROJECT_GOVERNANCE_"):
                code = "DURABLE_OWNER_READ_FAILED"
            elif code == "LOCAL_GOVERNANCE_ROOT_INVALID":
                code = "PROJECT_MISMATCH"
            elif code not in {
                "CURRENT_AUTHORITY_MISMATCH",
                "DURABLE_OWNER_READ_FAILED",
                "PROJECT_MISMATCH",
                "TERMINAL_PRECONDITION_FAILED",
                "TRUSTED_PROJECTION_CONTEXT_REQUIRED",
            }:
                code = "TERMINAL_PROJECTION_RECONCILIATION_FAILED"
            raise TerminalProjectionReconciliationError(
                code,
                "trusted terminal projection reconciliation failed",
            ) from exc

    return reconcile


def _verify_projection_readback(
    *,
    refreshed: ProjectionRefreshResult,
    project_id: str,
    plan_id: str,
    record: ProjectPlanRecord,
    document: Any,
    project_directory: Path,
) -> None:
    # ``refresh_for_trigger`` returns only after the existing lifecycle has
    # bounded-read back both generated files and matched their generated bytes.
    if (
        refreshed.bundle.project.project_id != project_id
        or refreshed.receipt.project_id != project_id
        or refreshed.map_path.parent != project_directory
        or refreshed.status_path.parent != project_directory
    ):
        raise TerminalProjectionReconciliationError(
            "PROJECTION_READ_BACK_MISMATCH",
            "generated Governance projection readback does not match the verified project output",
        )

    card = next((item for item in refreshed.bundle.plans if item.plan_id == plan_id), None)
    route = refreshed.context_route
    card_entries = route.entries_for_kind(ROUTE_ENTRY_PLAN_CARD)
    authority_entries = route.entries_for_kind(ROUTE_ENTRY_PLAN_AUTHORITY)
    if (
        card is None
        or card.lifecycle_state != record.lifecycle_state
        or card.plan_status != document.plan_status
        or card.authority_revision != str(record.authority.source_revision)
        or card.authority_digest != record.authority.source_digest
        or route.project_id != project_id
        or route.plan_id != plan_id
        or route.authority_source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        or not route.cards_present
        or not any(entry.projection_id == card.projection_id() for entry in card_entries)
        or not any(
            entry.ref == record.authority.authority_ref
            and entry.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
            and entry.revision == str(record.authority.source_revision)
            and entry.digest == record.authority.source_digest
            for entry in authority_entries
        )
    ):
        raise TerminalProjectionReconciliationError(
            "PROJECTION_READ_BACK_MISMATCH",
            "terminal Plan Card or ContextRoute does not match current Store and body truth",
        )


__all__ = [
    "TerminalProjectionReconciliationError",
    "TerminalProjectionReconciliationResult",
    "create_terminal_projection_reconciler",
]
