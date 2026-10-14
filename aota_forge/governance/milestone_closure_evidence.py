"""Thin, cross-owner evidence projection for M2 milestone closure.

Parent work-acceptance records are immutable evidence receipts. Execution and
result truth stays in the existing execution/handoff stores, runtime proof
stays with the trusted host-resource owner, and Plan identity is re-read from
the bound Local Governance source. This module owns no Work progression state.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from aota_forge.adapters.host import resources as host_resources
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    local_plan_authority_reference,
)
from aota_forge.core.contracts.canonical import canonical_json
from aota_forge.core.execution.durable_state import card_digest_for, is_bound_origin_session_ref
from aota_forge.core.journal.model import JournalState
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL, PortablePlanDocument
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
from aota_forge.governance.plan_semantics import (
    canonical_plan_amendment_digest,
    normalize_add_work_item,
)
from aota_forge.work_plane.handoff_store import handoff_open
from aota_forge.work_plane.milestone_closure import MilestoneClosureReadiness
from aota_forge.work_plane.milestone_review import ReviewCycle
from aota_forge.work_plane.result_card import ResultOutcome, WorkerResultCard
from aota_forge.work_plane.steward_finalizer import (
    ClosurePhase,
    FileBackedReceiptStore,
    GhCliGitHubPort,
    SubprocessGitPort,
    TrustedPlanIdentity,
    TrustedProjectBinding,
    TrustedStewardFinalizer,
    TrustedUserGateState,
)
from aota_forge.governance.stewardship import (
    GovernanceCheckpointKind,
    MaterializationRequest,
    SemanticFactSet,
    StewardshipCheckpoint,
    StewardshipDisposition,
    build_finalizer_input,
    evaluate_checkpoint,
)
from aota_forge.work_plane.handoff import SemanticReference

PARENT_WORK_ACCEPTANCE_SCHEMA_VERSION = 1
PARENT_WORK_ACCEPTANCE_IS_AUTHORITY = False
PARENT_WORK_ACCEPTANCE_IS_MUTABLE_STATE = False
PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION = False
NEW_WORK_LEDGER_CREATED = False
LEGACY_COORDINATOR_REQUIRED = False

# M2/W6 is deliberately a closure-repair seam, not a new Work progression
# ledger. The predecessor Work set is fixed by the accepted M2/W1-W5
# evidence contract; W6 is the repair/amendment Work Item itself and is never
# silently promoted to a required historical acceptance receipt.
M2_HISTORICAL_WORK_ITEMS: tuple[str, ...] = ("W1", "W2", "W3", "W4", "W5")
M2_W6_REPAIR_WORK_ITEM_ID = "W6"
HISTORICAL_WORK_ACCEPTANCE_RECONSTRUCTION = True
CURRENT_INSPECTOR_SESSION_IS_ACCEPTANCE_AUTHORITY = False
W6_SAME_PLAN_PREDECESSOR_REVISION_EXCEPTION_ONLY = True
GENERALIZED_PLAN_REVISION_ENGINE_CREATED = False
HISTORICAL_PLAN_LINEAGE_MAX_ENTRIES = 16

PARENT_WORK_ACCEPTANCE_DIRECTORY = "parent-work-acceptances"
PARENT_WORK_ACCEPTANCE_MAX_RECEIPTS = 64
LATEST_BY_TIME_SELECTION_ALLOWED = False
M2_W2_PRODUCTION_RUNTIME_ID = "plan005-m2-r8-runtime-fac1ad03ce75-e20377b18cca"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_MESSAGE_REF_RE = re.compile(r"^task-main-decision:(ses_[A-Za-z0-9]+):(msg_[A-Za-z0-9]+):([A-Za-z0-9._-]+)$")


class MilestoneClosureEvidenceError(ValueError):
    """Fail-closed error while projecting governed closure evidence."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _text(value: object, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", f"{label} must be text")
    if not value or value != value.strip() or len(value) > maximum or "\x00" in value:
        raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", f"{label} is invalid or unbounded")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", f"{label} must be a SHA-256 digest")
    return value


@dataclass(frozen=True)
class ParentWorkAcceptanceProjection:
    """Immutable parent decision receipt bound to one exact Work result."""

    project_id: str
    plan_id: str
    milestone_id: str
    work_item_id: str
    canonical_task_id: str
    result_ref: str
    result_digest: str
    completion_card_ref: str
    completion_card_digest: str
    acceptance_decision: str
    accepted_frontier: str
    parent_session_ref: str
    plan_source_revision: str
    decision_ref: str
    decision_digest: str
    production_evidence_ref: str | None = None
    production_evidence_digest: str | None = None
    production_provenance: Mapping[str, Any] | None = None
    production_provenance_digest: str | None = None
    receipt_digest: str = ""
    schema_version: int = PARENT_WORK_ACCEPTANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, str) or not PROJECT_ID_RE.fullmatch(self.project_id):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "project_id is not canonical")
        if not is_plan_id(self.plan_id):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "plan_id is not canonical")
        for field_name in (
            "plan_id",
            "milestone_id",
            "work_item_id",
            "canonical_task_id",
            "result_ref",
            "completion_card_ref",
            "parent_session_ref",
            "plan_source_revision",
            "decision_ref",
            "decision_digest",
        ):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if not is_bound_origin_session_ref(self.parent_session_ref):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "parent session reference is not exact")
        decision_match = _MESSAGE_REF_RE.fullmatch(self.decision_ref)
        if (
            decision_match is None
            or decision_match.group(1) != self.parent_session_ref
            or decision_match.group(3) != self.work_item_id
        ):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "decision ref does not bind the exact parent Work decision")
        _digest(self.result_digest, "result_digest")
        _digest(self.completion_card_digest, "completion_card_digest")
        if not isinstance(self.accepted_frontier, str) or not _COMMIT_RE.fullmatch(self.accepted_frontier):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "accepted_frontier must be a full Git commit SHA")
        _digest(self.decision_digest, "decision_digest")
        if self.acceptance_decision != "accepted":
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "only an accepted parent decision is projectable")
        if self.schema_version != PARENT_WORK_ACCEPTANCE_SCHEMA_VERSION:
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "unsupported parent acceptance receipt version")
        if self.production_evidence_ref is not None:
            object.__setattr__(self, "production_evidence_ref", _text(self.production_evidence_ref, "production_evidence_ref"))
        if self.production_evidence_digest is not None:
            _digest(self.production_evidence_digest, "production_evidence_digest")
        if (self.production_evidence_ref is None) != (self.production_evidence_digest is None):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "production evidence ref/digest must be paired")
        if self.production_provenance is not None:
            if not isinstance(self.production_provenance, Mapping):
                raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "production_provenance must be an object")
            try:
                normalized = json.loads(canonical_json(dict(self.production_provenance)))
            except (TypeError, ValueError) as exc:
                raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "production_provenance is not canonical JSON") from exc
            if len(canonical_json(normalized).encode("utf-8")) > 12 * 1024:
                raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "production_provenance exceeds its size bound")
            object.__setattr__(self, "production_provenance", normalized)
        if self.production_provenance_digest is not None:
            _digest(self.production_provenance_digest, "production_provenance_digest")
        if (self.production_provenance is None) != (self.production_provenance_digest is None):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "production provenance/digest must be paired")
        if self.production_provenance is not None:
            expected_proof_digest = hashlib.sha256(canonical_json(dict(self.production_provenance)).encode("utf-8")).hexdigest()
            if self.production_provenance_digest != expected_proof_digest:
                raise MilestoneClosureEvidenceError("EVIDENCE_DIGEST_MISMATCH", "W2 provenance projection digest mismatch")
        expected = self.compute_digest()
        if self.receipt_digest and self.receipt_digest != expected:
            raise MilestoneClosureEvidenceError("EVIDENCE_DIGEST_MISMATCH", "parent acceptance receipt digest mismatch")
        object.__setattr__(self, "receipt_digest", expected)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "acceptance_decision": self.acceptance_decision,
            "accepted_frontier": self.accepted_frontier,
            "canonical_task_id": self.canonical_task_id,
            "completion_card_digest": self.completion_card_digest,
            "completion_card_ref": self.completion_card_ref,
            "decision_ref": self.decision_ref,
            "decision_digest": self.decision_digest,
            "milestone_id": self.milestone_id,
            "parent_session_ref": self.parent_session_ref,
            "plan_id": self.plan_id,
            "plan_source_revision": self.plan_source_revision,
            "project_id": self.project_id,
            "production_evidence_digest": self.production_evidence_digest,
            "production_evidence_ref": self.production_evidence_ref,
            "production_provenance": self.production_provenance,
            "production_provenance_digest": self.production_provenance_digest,
            "result_digest": self.result_digest,
            "result_ref": self.result_ref,
            "schema_version": self.schema_version,
            "work_item_id": self.work_item_id,
        }

    def compute_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.canonical_payload()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "receipt_digest": self.receipt_digest}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParentWorkAcceptanceProjection":
        allowed = {
            "acceptance_decision", "accepted_frontier", "canonical_task_id",
            "completion_card_digest", "completion_card_ref", "decision_ref", "decision_digest",
            "milestone_id", "parent_session_ref", "plan_id", "plan_source_revision",
            "project_id", "production_evidence_digest", "production_evidence_ref",
            "production_provenance", "production_provenance_digest",
            "receipt_digest", "result_digest", "result_ref", "schema_version", "work_item_id",
        }
        extra = set(data) - allowed
        if extra:
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", f"unknown receipt fields: {sorted(extra)}")
        return cls(**dict(data))


class FileBackedParentWorkAcceptanceProjectionStore:
    """Content-addressed immutable receipts; this is not a Work state ledger."""

    def __init__(self, project_root: str | Path) -> None:
        self.root = Path(project_root) / ".aota" / PARENT_WORK_ACCEPTANCE_DIRECTORY

    def _ensure_root(self, *, create: bool) -> None:
        project_root = self.root.parent.parent
        state_dir = self.root.parent
        if project_root.is_symlink() or project_root.resolve(strict=True) != project_root:
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "Project root is not canonical")
        if state_dir.is_symlink():
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "Project runtime state root is a symlink")
        if not state_dir.exists():
            if not create:
                return
            state_dir.mkdir(mode=0o700)
        if not state_dir.is_dir():
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "Project runtime state root is not a directory")
        if not self.root.exists() and create:
            self.root.mkdir(mode=0o700)
        if self.root.exists() and (self.root.is_symlink() or not self.root.is_dir()):
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "acceptance receipt root is unsafe")

    def put_once(self, receipt: ParentWorkAcceptanceProjection) -> str:
        self._ensure_root(create=True)
        path = self.root / f"{receipt.receipt_digest}.json"
        payload = canonical_json(receipt.to_dict()).encode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".parent-acceptance-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                existing = self._read(path)
                if existing.receipt_digest != receipt.receipt_digest or existing.to_dict() != receipt.to_dict():
                    raise MilestoneClosureEvidenceError("EVIDENCE_STORE_CONFLICT", "immutable acceptance receipt conflicts")
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return f"parent-work-acceptance:{receipt.receipt_digest}"

    def list_for_milestone(self, project_id: str, plan_id: str, milestone_id: str) -> tuple[ParentWorkAcceptanceProjection, ...]:
        self._ensure_root(create=False)
        if not self.root.exists():
            return ()
        paths = sorted(self.root.iterdir(), key=lambda path: path.name)
        if len(paths) > PARENT_WORK_ACCEPTANCE_MAX_RECEIPTS:
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "acceptance receipt store exceeds its bound")
        receipts = []
        for path in paths:
            if path.suffix != ".json":
                continue
            receipt = self._read(path)
            if (receipt.project_id, receipt.plan_id, receipt.milestone_id) == (project_id, plan_id, milestone_id):
                receipts.append(receipt)
        return tuple(receipts)

    @staticmethod
    def _read(path: Path) -> ParentWorkAcceptanceProjection:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024:
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "acceptance receipt file is unsafe")
        try:
            return ParentWorkAcceptanceProjection.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except MilestoneClosureEvidenceError:
            raise
        except Exception as exc:
            raise MilestoneClosureEvidenceError("EVIDENCE_STORE_INVALID", "acceptance receipt cannot be verified") from exc


@dataclass(frozen=True)
class ThinMilestoneClosureEvidence:
    project_id: str
    plan_id: str
    milestone_id: str
    plan_source_revision: str
    work_acceptances: tuple[ParentWorkAcceptanceProjection, ...]
    accepted_frontier: str | None
    accepted_tree: str | None
    production_evidence_ref: str | None
    ready_for_project_steward: bool
    blocking_reasons: tuple[str, ...]
    readiness: MilestoneClosureReadiness | None


class ThinMilestoneClosureEvidenceAdapter:
    """Compose existing result, Plan, host-runtime, and parent decision owners."""

    def __init__(
        self,
        *,
        project_id: str,
        project_root: str | Path,
        governance_root: Any,
        plan_binding: PlanAuthorityBinding,
        parent_session_ref: str,
        execution_store: Any,
        sandbox: Any,
        governance_store: Any | None = None,
        receipt_store: FileBackedParentWorkAcceptanceProjectionStore | None = None,
        parent_decision_reader: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
        journal_store: Any | None = None,
        historical_plan_reader: Callable[[str], PortablePlanDocument | None] | None = None,
        plan_lineage_reader: Callable[..., Mapping[str, Any] | None] | None = None,
        host_config: Any | None = None,
        git_port: Any | None = None,
        handoff_reader=handoff_open,
        production_runtime_id: str = M2_W2_PRODUCTION_RUNTIME_ID,
    ) -> None:
        self.project_id = project_id
        self.project_root = Path(project_root)
        self.governance_root = governance_root
        self.plan_binding = plan_binding
        self.parent_session_ref = parent_session_ref
        self.execution_store = execution_store
        self.sandbox = sandbox
        self.governance_store = governance_store
        self.receipt_store = receipt_store or FileBackedParentWorkAcceptanceProjectionStore(project_root)
        self.parent_decision_reader = parent_decision_reader
        self.journal_store = journal_store
        self.historical_plan_reader = historical_plan_reader
        self.plan_lineage_reader = plan_lineage_reader
        self.host_config = host_config or host_resources.host_resource_config_from_env()
        self.git_port = git_port or SubprocessGitPort()
        self.handoff_reader = handoff_reader
        self.production_runtime_id = production_runtime_id

    def materialize_parent_acceptance(
        self,
        *,
        work_item_id: str,
        canonical_task_id: str,
        acceptance_decision: str,
        accepted_frontier: str,
        decision_ref: str,
        decision_digest: str,
        production_proof: Mapping[str, Any] | None = None,
        source_observation: Mapping[str, Any],
    ) -> ParentWorkAcceptanceProjection:
        plan, expected_work = self._current_plan()
        closure_work_items = self._closure_work_items(expected_work)
        if plan.current_milestone != "M2" or work_item_id not in closure_work_items:
            raise MilestoneClosureEvidenceError("WORK_IDENTITY_MISMATCH", "Work item is not in the bounded M2 predecessor Work set")
        if acceptance_decision != "accepted":
            raise MilestoneClosureEvidenceError("PARENT_DECISION_NOT_ACCEPTED", "only the existing accepted parent decision may be projected")
        if not canonical_task_id.startswith(f"{self.project_id}:{self.plan_binding.plan_id}:M2:{work_item_id}:"):
            raise MilestoneClosureEvidenceError("WORK_IDENTITY_MISMATCH", "canonical task does not bind the exact Work item")
        if not _COMMIT_RE.fullmatch(accepted_frontier) or not _SHA256_RE.fullmatch(decision_digest):
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "accepted frontier/decision digest is malformed")
        decision_ref = _text(decision_ref, "decision_ref")
        match = _MESSAGE_REF_RE.fullmatch(decision_ref)
        if match is None or match.group(1) != self.parent_session_ref or match.group(3) != work_item_id:
            raise MilestoneClosureEvidenceError("FOREIGN_PARENT_DECISION", "decision ref is not bound to this exact parent Work decision")
        self._verify_parent_decision(
            decision_ref=decision_ref,
            decision_digest=decision_digest,
            messages=self._read_parent_decision_messages(),
        )
        head = source_observation.get("head_sha")
        if not isinstance(head, str) or not _COMMIT_RE.fullmatch(head) or not self.git_port.is_ancestor(self.project_root, accepted_frontier, head):
            raise MilestoneClosureEvidenceError("ACCEPTED_FRONTIER_NOT_IN_SOURCE", "accepted frontier is not a trusted ancestor of current source")
        record = self.execution_store.get(canonical_task_id) if self.execution_store is not None else None
        if record is None or getattr(record, "canonical_task_id", None) != canonical_task_id:
            raise MilestoneClosureEvidenceError("WORK_RESULT_MISSING", "exact Work execution record is unavailable")
        card_raw = getattr(record, "worker_result_card", None)
        if not isinstance(card_raw, Mapping):
            raise MilestoneClosureEvidenceError("WORK_CARD_MISSING", "exact Work Result CARD is unavailable")
        card = WorkerResultCard.from_dict(card_raw)
        card_digest = card_digest_for(card.canonical_dict())
        task_state = getattr(getattr(record, "canonical_task_state", None), "value", getattr(record, "canonical_task_state", None))
        if (
            card.task_ref != canonical_task_id
            or card.outcome is not ResultOutcome.SUCCESS
            or card.semantic_stop is not None
            or card.mechanical_failure is not None
            or getattr(record, "worker_result_card_digest", None) != card_digest
            or task_state not in {"COMPLETED", "completed"}
        ):
            raise MilestoneClosureEvidenceError("WORK_CARD_INVALID", "exact Work Result CARD does not prove successful completion")
        result_digest = card.result_handoff_ref.digest
        if not isinstance(result_digest, str) or not _SHA256_RE.fullmatch(result_digest):
            raise MilestoneClosureEvidenceError("WORK_RESULT_INVALID", "exact result handoff digest is missing")
        opened = self.handoff_reader({"digest": result_digest}, sandbox=self.sandbox)
        envelope = opened.get("envelope")
        if (
            opened.get("digest") != result_digest
            or not isinstance(envelope, Mapping)
            or envelope.get("task_id") != canonical_task_id
            or envelope.get("project_id") != self.project_id
        ):
            raise MilestoneClosureEvidenceError("WORK_RESULT_MISMATCH", "result handoff does not bind the exact Work task")
        result_ref = f"handoff://{self.project_id}/{self.sandbox.worktree_id}/result/{envelope.get('artifact_id')}/{result_digest}"
        completion_card_ref = f"execution-result-card:{canonical_task_id}#sha256:{card_digest}"
        production_ref = production_digest = None
        production_provenance = production_provenance_digest = None
        if work_item_id == "W2":
            if self.production_runtime_id != M2_W2_PRODUCTION_RUNTIME_ID:
                raise MilestoneClosureEvidenceError("PRODUCTION_RESOURCE_MISMATCH", "W2 runtime resource id differs")
            production = self._w2_production_evidence(production_proof)
            production_ref = f"managed_deployment_receipt:{self.production_runtime_id}#sha256:{production['receipt_digest']}"
            production_digest = production["receipt_digest"]
            production_provenance = production["runtime_provenance"]
            production_provenance_digest = hashlib.sha256(
                canonical_json(production_provenance).encode("utf-8")
            ).hexdigest()
        elif production_proof is not None:
            raise MilestoneClosureEvidenceError("EVIDENCE_INVALID", "only W2 may bind production proof")
        receipt = ParentWorkAcceptanceProjection(
            project_id=self.project_id,
            plan_id=self.plan_binding.plan_id,
            milestone_id="M2",
            work_item_id=work_item_id,
            canonical_task_id=canonical_task_id,
            result_ref=result_ref,
            result_digest=result_digest,
            completion_card_ref=completion_card_ref,
            completion_card_digest=card_digest,
            acceptance_decision=acceptance_decision,
            accepted_frontier=accepted_frontier,
            parent_session_ref=self.parent_session_ref,
            plan_source_revision=str(self.plan_binding.source_revision),
            decision_ref=decision_ref,
            decision_digest=decision_digest,
            production_evidence_ref=production_ref,
            production_evidence_digest=production_digest,
            production_provenance=production_provenance,
            production_provenance_digest=production_provenance_digest,
        )
        self.receipt_store.put_once(receipt)
        return receipt

    def project(self, source_observation: Mapping[str, Any] | None) -> ThinMilestoneClosureEvidence:
        reasons: list[str] = []
        plan, work_items = self._current_plan()
        closure_work_items = self._closure_work_items(work_items)
        if plan.current_milestone != "M2":
            reasons.append("bound Plan current milestone is not M2")
        receipts = self.receipt_store.list_for_milestone(self.project_id, self.plan_binding.plan_id, "M2")
        selected: dict[str, ParentWorkAcceptanceProjection] = {}
        decision_messages_by_session: dict[str, dict[str, Mapping[str, Any]]] = {}
        decision_source_errors: dict[str, MilestoneClosureEvidenceError] = {}
        for receipt in receipts:
            historical_plan_receipt = False
            if receipt.work_item_id not in closure_work_items:
                reasons.append(f"foreign Work item acceptance: {receipt.work_item_id}")
                continue
            if receipt.plan_source_revision != str(self.plan_binding.source_revision):
                try:
                    self._verify_historical_plan_lineage(
                        receipt,
                        current_plan=plan,
                        required_work_items=closure_work_items,
                    )
                    historical_plan_receipt = True
                except MilestoneClosureEvidenceError as exc:
                    reasons.append(f"stale Plan source revision acceptance: {receipt.work_item_id}: {exc.code}")
                    continue
            # A different parent session is valid only when the receipt is a
            # verified predecessor-revision record. Current-revision receipts
            # may not smuggle in a foreign session.
            if receipt.parent_session_ref != self.parent_session_ref and not historical_plan_receipt:
                reasons.append(f"foreign parent session acceptance: {receipt.work_item_id}")
                continue
            expected_task_prefix = f"{self.project_id}:{self.plan_binding.plan_id}:M2:{receipt.work_item_id}:"
            if not receipt.canonical_task_id.startswith(expected_task_prefix):
                reasons.append(f"foreign canonical task acceptance: {receipt.work_item_id}")
                continue
            if receipt.work_item_id in selected:
                reasons.append(f"ambiguous accepted lineage for {receipt.work_item_id}")
                continue
            session_ref = receipt.parent_session_ref
            try:
                if session_ref in decision_source_errors:
                    raise decision_source_errors[session_ref]
                parent_messages = decision_messages_by_session.get(session_ref)
                if parent_messages is None:
                    parent_messages = self._read_parent_decision_messages(session_ref)
                    decision_messages_by_session[session_ref] = parent_messages
            except MilestoneClosureEvidenceError as exc:
                decision_source_errors.setdefault(session_ref, exc)
                if session_ref == self.parent_session_ref:
                    marker = f"parent decision source: {exc.code}"
                    if marker not in reasons:
                        reasons.append(marker)
                else:
                    reasons.append(f"{receipt.work_item_id}: {exc.code}")
                continue
            try:
                self._verify_parent_decision(
                    decision_ref=receipt.decision_ref,
                    decision_digest=receipt.decision_digest,
                    messages=parent_messages,
                )
                self._verify_result_lineage(receipt)
            except MilestoneClosureEvidenceError as exc:
                reasons.append(f"{receipt.work_item_id}: {exc.code}")
                continue
            selected[receipt.work_item_id] = receipt
        missing = sorted(set(closure_work_items) - set(selected))
        if missing:
            reasons.append(f"missing parent acceptance projection: {','.join(missing)}")
        final_acceptance = selected.get("W5")
        frontier = final_acceptance.accepted_frontier if final_acceptance is not None else None
        if frontier is None:
            reasons.append("W5 parent acceptance does not anchor the accepted source frontier")
        elif len(selected) > 0:
            try:
                for receipt in selected.values():
                    if not self.git_port.is_ancestor(self.project_root, receipt.accepted_frontier, frontier):
                        reasons.append(f"accepted source frontier mismatch for {receipt.work_item_id}")
            except Exception:
                reasons.append("accepted Work source frontier ancestry cannot be verified")
        tree = None
        head = source_observation.get("head_sha") if isinstance(source_observation, Mapping) else None
        if source_observation is None or source_observation.get("available") is not True or source_observation.get("complete") is not True:
            reasons.append("trusted Git source observation unavailable or incomplete")
        elif frontier is not None:
            try:
                if not isinstance(head, str) or not self.git_port.is_ancestor(self.project_root, frontier, head):
                    reasons.append("accepted source frontier is not an ancestor of current source")
                else:
                    tree = self.git_port.resolve_tree(self.project_root, frontier)
            except Exception:
                reasons.append("accepted source frontier cannot be verified by trusted Git owner")
        production_ref = None
        current_receipt_digest = None
        try:
            current_receipt_digest = self._resolve_w2_receipt_digest()
            production_ref = f"managed_deployment_receipt:{self.production_runtime_id}#sha256:{current_receipt_digest}"
        except MilestoneClosureEvidenceError as exc:
            reasons.append(f"W2 production evidence: {exc.code}")
        if "W2" in selected:
            receipt = selected["W2"]
            if (
                receipt.production_evidence_ref != production_ref
                or receipt.production_evidence_digest != current_receipt_digest
            ):
                reasons.append("W2 production receipt differs from accepted parent lineage")
            if not self._valid_stored_w2_provenance(receipt):
                reasons.append("W2 parent acceptance lacks trusted production proof projection")
            elif frontier is not None:
                runtime_subject = receipt.production_provenance.get("runtime_subject")
                runtime_source = runtime_subject.get("source_commit") if isinstance(runtime_subject, Mapping) else None
                runtime_tree = runtime_subject.get("source_tree") if isinstance(runtime_subject, Mapping) else None
                if (
                    not isinstance(runtime_source, str)
                    or not _COMMIT_RE.fullmatch(runtime_source)
                    or not isinstance(runtime_tree, str)
                    or not _COMMIT_RE.fullmatch(runtime_tree)
                    or runtime_subject.get("project_id") != self.project_id
                    or not self.git_port.is_ancestor(self.project_root, runtime_source, frontier)
                ):
                    reasons.append("W2 runtime production source is outside the accepted product frontier")
                else:
                    try:
                        if self.git_port.resolve_tree(self.project_root, runtime_source) != runtime_tree:
                            reasons.append("W2 runtime source tree does not match canonical Git")
                    except Exception:
                        reasons.append("W2 runtime source tree cannot be resolved by canonical Git")
        ready = not reasons and frontier is not None and tree is not None
        readiness = None
        if frontier is not None and tree is not None:
            readiness = MilestoneClosureReadiness(
                milestone_ref=SemanticReference(ref="M2"),
                ready_for_project_steward=ready,
                final_review_cycle=ReviewCycle.RV2,
                reviewed_frontier_ref=SemanticReference(ref=frontier),
                supporting_evidence_refs=tuple(
                    sorted({item.receipt_digest for item in selected.values()} | ({production_ref} if production_ref else set()))
                )[:16],
                blocking_reasons=tuple(sorted(set(reasons)))[:16],
            )
        return ThinMilestoneClosureEvidence(
            project_id=self.project_id,
            plan_id=self.plan_binding.plan_id,
            milestone_id="M2",
            plan_source_revision=str(self.plan_binding.source_revision),
            work_acceptances=tuple(selected[key] for key in sorted(selected)),
            accepted_frontier=frontier,
            accepted_tree=tree,
            production_evidence_ref=production_ref,
            ready_for_project_steward=ready,
            blocking_reasons=tuple(sorted(set(reasons))),
            readiness=readiness,
        )

    def close(self, *, source_observation: Mapping[str, Any]) -> dict[str, Any]:
        evidence = self.project(source_observation)
        if not evidence.ready_for_project_steward or evidence.readiness is None:
            return {"status": "blocked", "ready_for_project_steward": False, "blocking_reasons": list(evidence.blocking_reasons)}
        if self.governance_store is None:
            return {"status": "blocked", "ready_for_project_steward": True, "blocking_reasons": ["trusted user-gate owner unavailable"]}
        from aota_forge.composition.task_main_host_bootstrap import resolve_milestone_close_user_gate

        gate = resolve_milestone_close_user_gate(
            self.governance_store,
            project_id=self.project_id,
            plan_id=self.plan_binding.plan_id,
            milestone_id="M2",
        )
        if gate.user_approval_satisfied is not True:
            return {"status": "user_gate_required", "ready_for_project_steward": True, "accepted_frontier": evidence.accepted_frontier}
        self._current_plan()
        trusted_plan = TrustedPlanIdentity(
            governing_repo=None,
            plan_issue_number=None,
            milestone_ref="M2",
            plan_ref=self.plan_binding.authority_ref,
            managed_comments={},
            authority_binding=self.plan_binding,
        )
        checkpoint = StewardshipCheckpoint(
            checkpoint_id=f"milestone-close:{evidence.readiness.digest}",
            kind=GovernanceCheckpointKind.MILESTONE_CLOSE,
            project_id=self.project_id,
            trusted_binding=TrustedProjectBinding(
                project_id=self.project_id,
                binding_ref=f"worktree:{self.sandbox.worktree_id}",
                binding_digest=self.sandbox.digest,
            ),
            trusted_plan=trusted_plan,
            plan_id=self.plan_binding.plan_id,
            milestone_ref="M2",
            closure_phase=ClosurePhase.ACCEPTED_CLOSURE,
            readiness=evidence.readiness,
            user_gate=gate,
            materialization=MaterializationRequest(),
            semantic_facts=SemanticFactSet(),
            open_blocker_refs=(),
            plan_authority_ref=self.plan_binding.authority_ref,
        )
        evaluation = evaluate_checkpoint(checkpoint)
        if evaluation.disposition is not StewardshipDisposition.DETERMINISTIC_FINALIZABLE:
            return {"status": "blocked", "ready_for_project_steward": True, "blocking_reasons": list(evaluation.blocking_reasons)}
        finalizer = TrustedStewardFinalizer(
            git=SubprocessGitPort(),
            github=GhCliGitHubPort(),
            repo_path=self.project_root,
            receipt_store=FileBackedReceiptStore(self.project_root / ".aota" / "materialization_receipts.json"),
        )
        receipt = finalizer.finalize(
            build_finalizer_input(checkpoint),
            caller_role="governance.milestone.close",
            via_coordinator=False,
            via_milestone_close_ingress=True,
            has_closure_evidence=True,
        )
        return {
            "status": "accepted_closure",
            "project_id": evidence.project_id,
            "plan_id": evidence.plan_id,
            "milestone_id": evidence.milestone_id,
            "accepted_frontier": evidence.accepted_frontier,
            "receipt_ref": receipt.idempotency_key,
            "receipt_digest": receipt.receipt_digest,
            "ready_for_project_steward": True,
        }

    @staticmethod
    def _closure_work_items(expected_work: Any) -> tuple[str, ...]:
        """Return the fixed M2 predecessor set, never a model-selected set."""
        if isinstance(expected_work, Mapping):
            raw_items = tuple(expected_work.keys())
        elif isinstance(expected_work, Sequence) and not isinstance(expected_work, (str, bytes)):
            raw_items = tuple(expected_work)
        else:
            raise MilestoneClosureEvidenceError(
                "PLAN_WORK_SET_INVALID",
                "authoritative M2 Work set has an invalid shape",
            )
        if any(not isinstance(item, str) or not item.strip() for item in raw_items):
            raise MilestoneClosureEvidenceError(
                "PLAN_WORK_SET_INVALID",
                "authoritative M2 Work set contains an invalid Work identity",
            )
        if len(set(raw_items)) != len(raw_items):
            raise MilestoneClosureEvidenceError(
                "PLAN_WORK_SET_INVALID",
                "authoritative M2 Work set contains duplicate Work identities",
            )
        expected = set(raw_items)
        historical = set(M2_HISTORICAL_WORK_ITEMS)
        allowed = historical | {M2_W6_REPAIR_WORK_ITEM_ID}
        missing = historical - expected
        foreign = expected - allowed
        if missing or foreign:
            detail = []
            if missing:
                detail.append(f"missing={','.join(sorted(missing))}")
            if foreign:
                detail.append(f"foreign={','.join(sorted(foreign))}")
            raise MilestoneClosureEvidenceError(
                "PLAN_WORK_SET_INVALID",
                "authoritative M2 Work set is outside the bounded W1-W5/W6 contract ("
                + ";".join(detail)
                + ")",
            )
        return M2_HISTORICAL_WORK_ITEMS

    @staticmethod
    def _mapping_value(mapping: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    """
        self,\n        body: str,\n        relation: Mapping[str, Any],\n    ) -> str:\n        before_source = str(relation.get(\"authority_source_revision_before\") or \"\")\n        after_source = str(relation.get(\"authority_source_revision_after\") or \"\")\n        before_plan = relation.get(\"plan_revision_before\")\n        after_plan = relation.get(\"plan_revision_after\")\n        if (\n            not _SHA256_RE.fullmatch(before_source)\n            or not _SHA256_RE.fullmatch(after_source)\n            or isinstance(before_plan, bool)\n            or not isinstance(before_plan, int)\n            or isinstance(after_plan, bool)\n            or not isinstance(after_plan, int)\n            or after_plan != before_plan + 1\n        ):\n            raise MilestoneClosureEvidenceError(\n                \"PLAN_LINEAGE_REVISION_INVALID\",\n                \"W6 predecessor reconstruction requires adjacent source and Plan revisions\",\n            )\n        if self.plan_binding.source_revision is None or str(self.plan_binding.source_revision) != after_source:\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_PLAN_REVISION_MISMATCH\",\n                \"current bound Plan source revision is not the journal successor revision\",\n            )\n        current_digest = hashlib.sha256(body.encode(\"utf-8\")).hexdigest()\n        after_digest = str(relation.get(\"authority_source_digest_after\") or after_source)\n        if current_digest != after_source or current_digest != after_digest:\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_PLAN_DIGEST_MISMATCH\",\n                \"current Plan bytes do not match the journal successor source digest\",\n            )\n        before_digest = str(relation.get(\"authority_source_digest_before\") or before_source)\n        if before_digest != before_source:\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_PLAN_DIGEST_MISMATCH\",\n                \"journal predecessor revision and digest disagree\",\n            )\n        amendment = relation.get(\"amendment\")\n        if not isinstance(amendment, Mapping):\n            raise MilestoneClosureEvidenceError(\n                \"PLAN_LINEAGE_AMENDMENT_MISSING\",\n                \"journaled W6 amendment is unavailable for deterministic reconstruction\",\n            )\n        try:\n            normalized = normalize_add_work_item(amendment)\n            current_plan = normalize_portable_plan(\n                body,\n                source_revision=after_source,\n                source_kind=PORTABLE_PLAN_SOURCE_LOCAL,\n            )\n        except Exception as exc:\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_PLAN_RECONSTRUCTION_INVALID\",\n                \"current successor Plan cannot be normalized for W6 reconstruction\",\n            ) from exc\n\n        current_items = tuple(current_plan.milestone_work_items.get(\"M2\", ()))\n        expected_items = set(M2_HISTORICAL_WORK_ITEMS) | {M2_W6_REPAIR_WORK_ITEM_ID}\n        if set(current_items) != expected_items or len(current_items) != len(expected_items):\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_WORK_SEMANTICS_CHANGED\",\n                \"current M2 Work set is outside the bounded W1-W5/W6 successor shape\",\n            )\n        if str(current_plan.current_fields.get(\"PLAN_REVISION\") or \"\").strip() != f\"R{after_plan}\":\n            raise MilestoneClosureEvidenceError(\n                \"PLAN_LINEAGE_REVISION_INVALID\",\n                \"current Plan body does not carry the journal successor Plan revision\",\n            )\n        item = normalized[\"work_item\"]\n        edges: set[tuple[str, str]] = set()\n        for edge in current_plan.milestone_dependencies.get(\"M2\", ()):\n            if not isinstance(edge, (tuple, list)) or len(edge) != 2:\n                raise MilestoneClosureEvidenceError(\n                    \"HISTORICAL_WORK_SEMANTICS_INVALID\",\n                    \"current M2 dependency graph is not canonical\",\n                )\n            edges.add((str(edge[0]), str(edge[1])))\n        incoming_w6 = {source for source, target in edges if target == M2_W6_REPAIR_WORK_ITEM_ID}\n        if incoming_w6 != set(item[\"dependencies\"]) or any(source == M2_W6_REPAIR_WORK_ITEM_ID for source, _ in edges):\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_WORK_SEMANTICS_CHANGED\",\n                \"current M2 dependency graph is not the bounded W6 successor relation\",\n            )\n        predecessor_edges = {\n            edge\n            for edge in edges\n            if M2_W6_REPAIR_WORK_ITEM_ID not in edge\n        }\n        if any(\n            source not in M2_HISTORICAL_WORK_ITEMS or target not in M2_HISTORICAL_WORK_ITEMS\n            for source, target in predecessor_edges\n        ):\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_WORK_SEMANTICS_CHANGED\",\n                \"W1-W5 dependency semantics are not bounded by the successor relation\",\n            )\n\n        edge_nodes = {node for edge in predecessor_edges for node in edge}\n        predecessor_dag = [\n            f\"{source} -> {target}\"\n            for source, target in sorted(predecessor_edges)\n        ]\n        predecessor_dag.extend(\n            sorted(set(M2_HISTORICAL_WORK_ITEMS) - edge_nodes)\n        )\n        ending = \"\\r\\n\" if \"\\r\\n\" in body else \"\\n\"\n        block_lines = [\n            f\"#### M2/{item['id']} - {item['title']}\",\n            f\"Objective: {item['objective']}\",\n            \"Scope:\",\n            *(f\"- {entry}\" for entry in item[\"scope\"]),\n            f\"Dependencies: {', '.join(item['dependencies']) if item['dependencies'] else 'none'}\",\n            \"Acceptance:\",\n            *(f\"- {entry}\" for entry in item[\"acceptance\"]),\n        ]\n        rendered = ending.join(block_lines)\n        if body.count(rendered) != 1:\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_PLAN_RECONSTRUCTION_AMBIGUOUS\",\n                \"the canonical M2/W6 successor block is not singular\",\n            )\n        start = body.index(rendered)\n        stop = start + len(rendered)\n        current_work_items = str(current_plan.current_fields.get(\"M2_WORK_ITEMS\") or \"\").strip()\n        current_dag = str(current_plan.current_fields.get(\"M2_DAG\") or \"\").strip()\n        current_revision = f\"R{after_plan}\"\n        for left in range(3):\n            left_start = start - (left * len(ending))\n            if body[left_start:start] != ending * left:\n                continue\n            for right in range(3):\n                right_stop = stop + (right * len(ending))\n                if body[stop:right_stop] != ending * right:\n                    continue\n                candidate = body[:left_start] + body[right_stop:]\n                try:\n                    candidate = self._replace_reconstructed_field(\n                        candidate,\n                        \"PLAN_REVISION\",\n                        current_revision,\n                        f\"R{before_plan}\",\n                    )\n                    candidate = self._replace_reconstructed_field(\n                        candidate,\n                        \"M2_WORK_ITEMS\",\n                        current_work_items,\n                        \", \".join(M2_HISTORICAL_WORK_ITEMS),\n                    )\n                    candidate = self._replace_reconstructed_field(\n                        candidate,\n                        \"M2_DAG\",\n                        current_dag,\n                        \", \".join(predecessor_dag),\n                    )\n                    reconstructed = normalize_portable_plan(\n                        candidate,\n                        source_revision=before_source,\n                        source_kind=PORTABLE_PLAN_SOURCE_LOCAL,\n                    )\n                except Exception:\n                    continue\n\n                if (\n                    hashlib.sha256(candidate.encode(\"utf-8\")).hexdigest() == before_digest\n                    and set(reconstructed.milestone_work_items.get(\"M2\", ())) == set(M2_HISTORICAL_WORK_ITEMS)\n                    and M2_W6_REPAIR_WORK_ITEM_ID not in reconstructed.milestone_work_items.get(\"M2\", ())\n                    and set(reconstructed.milestone_dependencies.get(\"M2\", ())) == predecessor_edges\n                ):\n                    return candidate\n        raise MilestoneClosureEvidenceError(\n            \"HISTORICAL_PLAN_RECONSTRUCTION_MISMATCH\",\n            \"bounded reverse W6 reconstruction did not reproduce the journal predecessor digest\",\n        )\n\n    def _reconstruct_historical_plan_from_journal(\n        self,\n        source_revision: str,\n        receipt: ParentWorkAcceptanceProjection,\n    ) -> Mapping[str, Any] | None:\n        if self.journal_store is None:\n            return None\n        relation = self._journal_plan_lineage(receipt)\n        if relation is None:\n            return None\n        if str(relation.get(\"authority_source_revision_before\") or \"\") != source_revision:\n            return None\n        try:\n            destination = LocalPlanAuthorityDestination(\n                self.governance_root,\n                self.plan_binding.plan_id,\n                _plan_object_ref(self.plan_binding.plan_id),\n            )\n            snapshot = LocalPlanAuthorityReadAdapter(\n                destination,\n                binding=self.plan_binding,\n            ).load()\n        except Exception as exc:\n            raise MilestoneClosureEvidenceError(\n                \"HISTORICAL_PLAN_SOURCE_UNAVAILABLE\",\n                \"trusted current local Plan source could not be read for reconstruction\",\n            ) from exc\n        body = self._reverse_w6_predecessor_body(snapshot.body, relation)\n        return {\n            \"body\": body,\n            \"source_revision\": source_revision,\n            \"project_id\": self.project_id,\n            \"plan_id\": self.plan_binding.plan_id,\n        }

    """

    @staticmethod
    def _replace_reconstructed_field(body: str, key: str, expected: str, replacement: str) -> str:
        pattern = re.compile(rf"(?m)^{re.escape(key)}=(?P<value>[^\r\n]*)(?P<ending>\r?\n|$)")
        matches = tuple(pattern.finditer(body))
        if len(matches) != 1 or matches[0].group("value").strip() != expected.strip():
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_RECONSTRUCTION_INVALID",
                f"canonical field {key} is not the expected singular successor field",
            )
        match = matches[0]
        return body[:match.start()] + f"{key}={replacement}{match.group('ending')}" + body[match.end():]

    def _reverse_w6_predecessor_body(self, body: str, relation: Mapping[str, Any]) -> str:
        return self._reverse_w6_predecessor_body_impl(body, relation)

    def _reverse_w6_predecessor_body_impl(self, body: str, relation: Mapping[str, Any]) -> str:
        before, after, before_plan, after_plan, before_digest, current_plan = self._w6_lineage_bounds(body, relation)
        amendment, predecessor_edges, predecessor_dag, block = self._w6_successor_shape(body, current_plan, relation)
        return self._w6_find_predecessor(
            body, before, before_plan, before_digest, after_plan, current_plan,
            predecessor_edges, predecessor_dag, block,
        )

    def _w6_lineage_bounds(self, body: str, relation: Mapping[str, Any]):
        before = str(self._mapping_value(relation, "authority_source_revision_before", "source_revision_before", "predecessor_source_revision") or "")
        after = str(self._mapping_value(relation, "authority_source_revision_after", "source_revision_after") or "")
        before_plan = relation.get("plan_revision_before")
        after_plan = relation.get("plan_revision_after")
        if not (_SHA256_RE.fullmatch(before) and _SHA256_RE.fullmatch(after)):
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_REVISION_INVALID", "journal source revisions are invalid")
        if (isinstance(before_plan, bool) or not isinstance(before_plan, int) or isinstance(after_plan, bool) or not isinstance(after_plan, int) or after_plan != before_plan + 1):
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_REVISION_INVALID", "journal Plan revisions are not adjacent")
        if self.plan_binding.source_revision is None or str(self.plan_binding.source_revision) != after:
            raise MilestoneClosureEvidenceError("HISTORICAL_PLAN_REVISION_MISMATCH", "successor revision is not bound")
        current_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        after_digest = str(relation.get("authority_source_digest_after") or relation.get("observed_raw_digest") or after)
        before_digest = str(relation.get("authority_source_digest_before") or relation.get("original_plan_raw_digest") or before)
        if current_digest != after or current_digest != after_digest or before_digest != before:
            raise MilestoneClosureEvidenceError("HISTORICAL_PLAN_DIGEST_MISMATCH", "journal/source digest mismatch")
        try:
            current_plan = normalize_portable_plan(body, source_revision=after, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
        except Exception as exc:
            raise MilestoneClosureEvidenceError("HISTORICAL_PLAN_RECONSTRUCTION_INVALID", "successor Plan is invalid") from exc
        return before, after, before_plan, after_plan, before_digest, current_plan

    def _w6_successor_shape(self, body: str, current_plan: PortablePlanDocument, relation: Mapping[str, Any]):
        raw = relation.get("amendment")
        if not isinstance(raw, Mapping):
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_AMENDMENT_MISSING", "W6 amendment is unavailable")
        try:
            amendment = normalize_add_work_item(raw)
            expected_digest = canonical_plan_amendment_digest(amendment)
        except Exception as exc:
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_AMENDMENT_INVALID", "W6 amendment is not canonical") from exc
        if relation.get("amendment_digest") != expected_digest:
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_AMENDMENT_DIGEST_MISMATCH", "W6 amendment digest mismatch")
        item = amendment["work_item"]
        if amendment["milestone_id"] != "M2" or item["id"] != M2_W6_REPAIR_WORK_ITEM_ID:
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_NOT_W6_REPAIR", "amendment is not M2/W6")
        items = tuple(current_plan.milestone_work_items.get("M2", ()))
        expected_items = set(M2_HISTORICAL_WORK_ITEMS) | {M2_W6_REPAIR_WORK_ITEM_ID}
        if set(items) != expected_items or len(items) != len(expected_items):
            raise MilestoneClosureEvidenceError("HISTORICAL_WORK_SEMANTICS_CHANGED", "successor Work set is outside W1-W5/W6")
        after_plan = relation.get("plan_revision_after")
        revision_match = re.search(r"(?m)^PLAN_REVISION=([^\r\n]+)", body)
        if revision_match is None or revision_match.group(1).strip() != f"R{after_plan}":
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_REVISION_INVALID", "successor body revision disagrees with journal")
        edges = set()
        for edge in current_plan.milestone_dependencies.get("M2", ()):
            if not isinstance(edge, (tuple, list)) or len(edge) != 2:
                raise MilestoneClosureEvidenceError("HISTORICAL_WORK_SEMANTICS_INVALID", "successor DAG is invalid")
            edges.add((str(edge[0]), str(edge[1])))
        incoming = {source for source, target in edges if target == M2_W6_REPAIR_WORK_ITEM_ID}
        if incoming != set(item["dependencies"]) or any(source == M2_W6_REPAIR_WORK_ITEM_ID for source, _ in edges):
            raise MilestoneClosureEvidenceError("HISTORICAL_WORK_SEMANTICS_CHANGED", "successor W6 relation is invalid")
        predecessor_edges = {edge for edge in edges if M2_W6_REPAIR_WORK_ITEM_ID not in edge}
        if any(source not in M2_HISTORICAL_WORK_ITEMS or target not in M2_HISTORICAL_WORK_ITEMS for source, target in predecessor_edges):
            raise MilestoneClosureEvidenceError("HISTORICAL_WORK_SEMANTICS_CHANGED", "W1-W5 DAG is not bounded")
        nodes = {node for edge in predecessor_edges for node in edge}
        predecessor_dag = [f"{source} -> {target}" for source, target in sorted(predecessor_edges)]
        predecessor_dag.extend(sorted(set(M2_HISTORICAL_WORK_ITEMS) - nodes))
        ending = "\r\n" if "\r\n" in body else "\n"
        block = ending.join([
            f"#### M2/{item['id']} - {item['title']}", f"Objective: {item['objective']}", "Scope:",
            *(f"- {entry}" for entry in item["scope"]),
            f"Dependencies: {', '.join(item['dependencies']) if item['dependencies'] else 'none'}", "Acceptance:",
            *(f"- {entry}" for entry in item["acceptance"]),
        ])
        return amendment, predecessor_edges, predecessor_dag, block

    def _w6_find_predecessor(
        self,
        body: str,
        before: str,
        before_plan: int,
        before_digest: str,
        after_plan: int,
        current_plan: PortablePlanDocument,
        predecessor_edges: set[tuple[str, str]],
        predecessor_dag: Sequence[str],
        block: str,
    ) -> str:
        ending = "\r\n" if "\r\n" in body else "\n"
        start = body.index(block)
        stop = start + len(block)
        current_items = str(current_plan.current_fields.get("M2_WORK_ITEMS") or "").strip()
        current_dag = str(current_plan.current_fields.get("M2_DAG") or "").strip()
        dag_values = [", ".join(predecessor_dag)]
        outgoing: dict[str, str] = {}
        incoming: set[str] = set()
        for source, target in predecessor_edges:
            if source in outgoing:
                outgoing[source] = ""
            else:
                outgoing[source] = target
            incoming.add(target)
        starts = set(M2_HISTORICAL_WORK_ITEMS) - incoming
        chain: list[str] = []
        if len(starts) == 1:
            node = next(iter(starts))
            while node and node not in chain:
                chain.append(node)
                node = outgoing.get(node, "")
        if len(chain) == len(M2_HISTORICAL_WORK_ITEMS) and len(predecessor_edges) == len(M2_HISTORICAL_WORK_ITEMS) - 1:
            dag_values.append(" -> ".join(chain))
        for dag_value in dag_values:
            for left in range(3):
                left_start = start - left * len(ending)
                if left_start < 0 or body[left_start:start] != ending * left:
                    continue
                for right in range(3):
                    right_stop = stop + right * len(ending)
                    if right_stop > len(body) or body[stop:right_stop] != ending * right:
                        continue
                    candidate = body[:left_start] + body[right_stop:]
                    try:
                        candidate = self._replace_reconstructed_field(candidate, "PLAN_REVISION", f"R{after_plan}", f"R{before_plan}")
                        candidate = self._replace_reconstructed_field(candidate, "M2_WORK_ITEMS", current_items, ", ".join(M2_HISTORICAL_WORK_ITEMS))
                        candidate = self._replace_reconstructed_field(candidate, "M2_DAG", current_dag, dag_value)
                        document = normalize_portable_plan(candidate, source_revision=before, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
                    except Exception:
                        continue
                    if (
                        hashlib.sha256(candidate.encode("utf-8")).hexdigest() == before_digest
                        and set(document.milestone_work_items.get("M2", ())) == set(M2_HISTORICAL_WORK_ITEMS)
                        and M2_W6_REPAIR_WORK_ITEM_ID not in document.milestone_work_items.get("M2", ())
                        and set(document.milestone_dependencies.get("M2", ())) == predecessor_edges
                    ):
                        return candidate
        raise MilestoneClosureEvidenceError("HISTORICAL_PLAN_RECONSTRUCTION_MISMATCH", "reverse W6 digest did not match")

    def _reconstruct_historical_plan_from_journal(
        self,
        source_revision: str,
        receipt: ParentWorkAcceptanceProjection,
    ) -> Mapping[str, Any] | None:
        if self.journal_store is None:
            return None
        relation = self._journal_plan_lineage(receipt)
        if relation is None or str(relation.get("authority_source_revision_before") or "") != source_revision:
            return None
        try:
            destination = LocalPlanAuthorityDestination(
                self.governance_root,
                self.plan_binding.plan_id,
                _plan_object_ref(self.plan_binding.plan_id),
            )
            snapshot = LocalPlanAuthorityReadAdapter(destination, binding=self.plan_binding).load()
        except Exception as exc:
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_SOURCE_UNAVAILABLE",
                "trusted current local Plan source could not be read for reconstruction",
            ) from exc
        return {
            "body": self._reverse_w6_predecessor_body(snapshot.body, relation),
            "source_revision": source_revision,
            "project_id": self.project_id,
            "plan_id": self.plan_binding.plan_id,
        }

    def _read_historical_plan(
        self,
        source_revision: str,
        receipt: ParentWorkAcceptanceProjection,
    ) -> tuple[PortablePlanDocument, Mapping[str, Any]]:
        reader = self.historical_plan_reader
        if callable(reader):
            try:
                document = reader(source_revision)
            except Exception as exc:
                raise MilestoneClosureEvidenceError(
                    "HISTORICAL_PLAN_SOURCE_UNAVAILABLE",
                    "trusted predecessor Plan source could not be read",
                ) from exc
            if document is not None:
                if not isinstance(document, PortablePlanDocument):
                    raise MilestoneClosureEvidenceError(
                        "HISTORICAL_PLAN_SOURCE_INVALID",
                        "predecessor Plan source is not a normalized Plan document",
                    )
                if str(document.source_revision) != str(source_revision):
                    raise MilestoneClosureEvidenceError(
                        "HISTORICAL_PLAN_DIGEST_MISMATCH",
                        "predecessor Plan source revision does not match the receipt",
                    )
                return document, {"project_id": self.project_id, "plan_id": self.plan_binding.plan_id}
        relation = self._journal_plan_lineage(receipt)
        if relation is None:
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_SOURCE_UNAVAILABLE",
                "exact predecessor Plan relation is unavailable",
            )
        body = relation.get("last_observed_raw_authority_body")
        if isinstance(body, str) and hashlib.sha256(body.encode("utf-8")).hexdigest() == source_revision:
            try:
                return (
                    normalize_portable_plan(
                        body,
                        source_revision=source_revision,
                        source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
                    ),
                    {"project_id": self.project_id, "plan_id": self.plan_binding.plan_id},
                )
            except Exception as exc:
                raise MilestoneClosureEvidenceError(
                    "HISTORICAL_PLAN_SOURCE_INVALID",
                    "journal predecessor Plan body is not a valid normalized Plan",
                ) from exc
        reconstructed = self._reconstruct_historical_plan_from_journal(source_revision, receipt)
        if not isinstance(reconstructed, Mapping) or not isinstance(reconstructed.get("body"), str):
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_SOURCE_UNAVAILABLE",
                "exact predecessor Plan body is unavailable",
            )
        predecessor_body = reconstructed["body"]
        if hashlib.sha256(predecessor_body.encode("utf-8")).hexdigest() != source_revision:
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_DIGEST_MISMATCH",
                "reconstructed predecessor Plan digest differs from the journal predecessor revision",
            )
        try:
            document = normalize_portable_plan(
                predecessor_body,
                source_revision=source_revision,
                source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
            )
        except Exception as exc:
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_SOURCE_INVALID",
                "reconstructed predecessor Plan is not valid",
            ) from exc
        return document, {
            "project_id": self.project_id,
            "plan_id": self.plan_binding.plan_id,
        }

    @staticmethod
    def _work_projection(plan: PortablePlanDocument, work_item_id: str) -> dict[str, Any]:
        slices: list[dict[str, Any]] = []
        for item in getattr(plan, "work_source_slices", {}).get("M2", ()):
            item_id = item.get("work_item_id") if isinstance(item, Mapping) else getattr(item, "work_item_id", None)
            if item_id == work_item_id:
                try:
                    slices.append(item.to_dict() if hasattr(item, "to_dict") else dict(item))
                except Exception as exc:
                    raise MilestoneClosureEvidenceError(
                        "HISTORICAL_WORK_SEMANTICS_INVALID",
                        f"M2/{work_item_id} source slice is not canonical",
                    ) from exc
        field_projection: dict[str, Any] = {}
        pattern = re.compile(rf"(?:^|[_:/.-]){re.escape(work_item_id)}(?:$|[_:/.-])", re.IGNORECASE)
        for key, value in getattr(plan, "current_fields", {}).items():
            if isinstance(key, str) and pattern.search(key):
                field_projection[key] = value
        dependencies = []
        for edge in getattr(plan, "milestone_dependencies", {}).get("M2", ()):
            if isinstance(edge, (tuple, list)) and len(edge) == 2 and work_item_id in edge:
                dependencies.append(list(edge))
        return {
            "work_item_id": work_item_id,
            "source_slices": sorted(slices, key=lambda item: canonical_json(item)),
            "fields": field_projection,
            "dependencies": sorted(dependencies, key=lambda item: canonical_json(item)),
        }

    def _read_plan_lineage(
        self,
        receipt: ParentWorkAcceptanceProjection,
        _current_plan: PortablePlanDocument,
    ) -> Mapping[str, Any]:
        reader = self.plan_lineage_reader
        if callable(reader):
            try:
                relation = reader(receipt, _current_plan)
            except Exception as exc:
                raise MilestoneClosureEvidenceError(
                    "PLAN_LINEAGE_SOURCE_UNAVAILABLE",
                    "trusted predecessor Plan lineage source could not be read",
                ) from exc
            if relation is None:
                raise MilestoneClosureEvidenceError(
                    "PLAN_LINEAGE_SOURCE_UNAVAILABLE",
                    "exact same-Plan predecessor relation is unavailable",
                )
            if not isinstance(relation, Mapping):
                raise MilestoneClosureEvidenceError(
                    "PLAN_LINEAGE_SOURCE_INVALID",
                    "predecessor Plan lineage source is not canonical",
                )
            return relation
        if self.journal_store is None:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_SOURCE_UNAVAILABLE",
                "trusted durable journal is unavailable",
            )
        relation = self._journal_plan_lineage(receipt)
        if relation is None:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_SOURCE_UNAVAILABLE",
                "exact same-Plan predecessor relation is unavailable",
            )
        return relation


    def _journal_lineage_records(self) -> Sequence[Any]:
        if self.journal_store is None:
            return ()
        try:
            entries = self.journal_store.list_all()
        except Exception as exc:
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_SOURCE_UNAVAILABLE", "trusted durable journal could not be read") from exc
        if len(entries) > HISTORICAL_PLAN_LINEAGE_MAX_ENTRIES * 4:
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_UNBOUNDED", "durable journal lineage scan exceeds its bound")
        return tuple(getattr(entry, "record", entry) for entry in entries)

    def _journal_lineage_relation(self, record: Any) -> dict[str, Any] | None:
        evidence = getattr(record, "evidence", {})
        if not isinstance(evidence, Mapping):
            return None
        relation = dict(evidence)
        for key in ("lineage", "amendment_readback", "plan_amendment", "journal_cas_outcome"):
            nested = relation.get(key)
            if isinstance(nested, Mapping):
                relation.update(nested)
        state = getattr(getattr(record, "journal_state", None), "value", getattr(record, "journal_state", None))
        if state is not None:
            evidence_state = relation.get("journal_state")
            if evidence_state is not None and getattr(evidence_state, "value", evidence_state) != state:
                raise MilestoneClosureEvidenceError("PLAN_LINEAGE_JOURNAL_INVALID", "journal evidence state disagrees with JournalRecord")
            relation["journal_state"] = state

        def bind(key: str, value: Any) -> None:
            if value is None:
                return
            existing = relation.get(key)
            if existing is not None and str(existing) != str(value):
                raise MilestoneClosureEvidenceError("PLAN_LINEAGE_RECORD_MISMATCH", f"journal evidence {key} disagrees with JournalRecord")
            relation[key] = value

        bind("authority_source_revision_before", getattr(record, "authority_source_revision", None))
        bind("authority_source_revision_after", getattr(record, "observed_revision", None))
        bind("authority_source_digest_before", getattr(record, "authority_observed_raw_digest", None) or getattr(record, "original_raw_digest", None))
        bind("authority_source_digest_after", getattr(record, "observed_raw_digest", None))
        subject_revision = getattr(record, "subject_expected_revision", None)
        if subject_revision is not None:
            relation.setdefault("plan_revision_before", subject_revision)
        decision = relation.get("semantic_decision_receipt") or relation.get("plan_decision_receipt")
        if isinstance(decision, Mapping):
            for key in ("project_id", "plan_id", "amendment", "amendment_digest"):
                if key in decision:
                    relation.setdefault(key, decision[key])
            relation.setdefault("plan_revision_before", decision.get("base_plan_revision"))
        amendment = relation.get("amendment")
        if isinstance(amendment, Mapping):
            relation.setdefault("milestone_id", amendment.get("milestone_id"))
            item = amendment.get("work_item")
            if isinstance(item, Mapping):
                relation.setdefault("work_item_id", item.get("id"))
        if self.governance_store is not None:
            try:
                current = self.governance_store.get_plan(self.project_id, self.plan_binding.plan_id)
            except Exception as exc:
                raise MilestoneClosureEvidenceError("PLAN_LINEAGE_SOURCE_UNAVAILABLE", "trusted Project Governance Store could not be read") from exc
            if current is not None:
                relation.setdefault("project_id", current.project_id)
                relation.setdefault("plan_id", current.plan_id)
                relation.setdefault("plan_revision_after", current.revision)
                relation.setdefault("current_plan_source_revision", current.authority.source_revision)
                relation.setdefault("current_plan_source_digest", current.authority.source_digest)
        return relation

    def _journal_plan_lineage(self, receipt: ParentWorkAcceptanceProjection) -> Mapping[str, Any] | None:
        matches = []
        for record in self._journal_lineage_records():
            relation = self._journal_lineage_relation(record)
            if relation is None:
                continue
            if (
                relation.get("project_id") == self.project_id
                and relation.get("plan_id") == self.plan_binding.plan_id
                and str(relation.get("authority_source_revision_before") or "") == str(receipt.plan_source_revision)
                and str(relation.get("authority_source_revision_after") or "") == str(self.plan_binding.source_revision)
            ):
                relation["journal_record"] = record
                matches.append(relation)
        if len(matches) > 1:
            raise MilestoneClosureEvidenceError("PLAN_LINEAGE_AMBIGUOUS", "multiple durable Plan predecessor relations match the receipt")
        return matches[0] if matches else None

    def _verify_historical_plan_lineage(
        self,
        receipt: ParentWorkAcceptanceProjection,
        *,
        current_plan: PortablePlanDocument,
        required_work_items: Sequence[str],
    ) -> None:
        predecessor_revision = str(receipt.plan_source_revision)
        current_revision = str(self.plan_binding.source_revision)
        if predecessor_revision == current_revision:
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_REQUIRED",
                "historical validation was requested for the current Plan revision",
            )
        if tuple(required_work_items) != M2_HISTORICAL_WORK_ITEMS:
            raise MilestoneClosureEvidenceError(
                "PLAN_WORK_SET_INVALID",
                "historical validation requires exactly the bounded M2/W1-W5 set",
            )
        lineage = self._read_plan_lineage(receipt, current_plan)
        historical_plan, _metadata = self._read_historical_plan(predecessor_revision, receipt)
        if historical_plan.current_milestone != "M2" or current_plan.current_milestone != "M2":
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_MILESTONE_MISMATCH",
                "predecessor/current Plan is not bound to M2",
            )
        self._closure_work_items(historical_plan.milestone_work_items.get("M2", ()))
        for work_item_id in M2_HISTORICAL_WORK_ITEMS:
            if self._work_projection(historical_plan, work_item_id) != self._work_projection(current_plan, work_item_id):
                raise MilestoneClosureEvidenceError(
                    "HISTORICAL_WORK_SEMANTICS_CHANGED",
                    f"M2/{work_item_id} acceptance or semantic projection changed",
                )
        stable_current = {
            "current_milestone": current_plan.current_milestone,
            "current_status": current_plan.current_status,
            "milestone_status": current_plan.milestone_status.get("M2"),
            "milestone_approval": current_plan.milestone_approvals.get("M2"),
        }
        stable_historical = {
            "current_milestone": historical_plan.current_milestone,
            "current_status": historical_plan.current_status,
            "milestone_status": historical_plan.milestone_status.get("M2"),
            "milestone_approval": historical_plan.milestone_approvals.get("M2"),
        }
        if stable_current != stable_historical:
            raise MilestoneClosureEvidenceError(
                "HISTORICAL_PLAN_STATUS_CHANGED",
                "M2 status/approval changed across the predecessor revision",
            )

        lineage = self._read_plan_lineage(receipt, current_plan)
        project_id = self._mapping_value(lineage, "project_id")
        plan_id = self._mapping_value(lineage, "plan_id")
        if project_id != self.project_id or plan_id != self.plan_binding.plan_id:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_IDENTITY_MISMATCH",
                "predecessor relation is not for the bound project and Plan",
            )
        if self._mapping_value(lineage, "milestone_id", "milestone_ref") != "M2":
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_IDENTITY_MISMATCH",
                "predecessor relation is not for M2",
            )
        if self._mapping_value(lineage, "work_item_id", "work_item_ref") != M2_W6_REPAIR_WORK_ITEM_ID:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_NOT_W6_REPAIR",
                "historical Plan exception is not explicitly bound to W6",
            )
        before = self._mapping_value(
            lineage,
            "authority_source_revision_before",
            "plan_digest_before",
            "source_revision_before",
            "predecessor_source_revision",
        )
        after = self._mapping_value(
            lineage,
            "authority_source_revision_after",
            "plan_digest_after",
            "source_revision_after",
        )
        if str(before) != predecessor_revision or str(after) != current_revision:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_REVISION_MISMATCH",
                "predecessor relation does not bind both Plan source revisions",
            )

        before_plan_revision = self._mapping_value(lineage, "plan_revision_before", "revision_before")
        after_plan_revision = self._mapping_value(lineage, "plan_revision_after", "revision_after")
        if (
            isinstance(before_plan_revision, bool)
            or not isinstance(before_plan_revision, int)
            or isinstance(after_plan_revision, bool)
            or not isinstance(after_plan_revision, int)
            or after_plan_revision != before_plan_revision + 1
        ):
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_REVISION_INVALID",
                "W6 exception must be one immediate predecessor Plan revision",
            )
        journal_state = self._mapping_value(lineage, "journal_state", "cas_state")
        journal_state = getattr(journal_state, "value", journal_state)
        if journal_state not in {JournalState.VERIFIED.value, JournalState.VERIFIED_RECOVERED.value}:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_JOURNAL_INVALID",
                "W6 amendment journal evidence is not terminally verified",
            )

        amendment = self._mapping_value(lineage, "amendment", "amendment_payload", "plan_amendment")
        if not isinstance(amendment, Mapping):
            event = lineage.get("event")
            amendment = event.get("amendment") if isinstance(event, Mapping) else None
        if not isinstance(amendment, Mapping):
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_AMENDMENT_MISSING",
                "W6 amendment payload is unavailable",
            )
        work_item = amendment.get("work_item")
        if (
            amendment.get("kind") != "add_work_item"
            or amendment.get("milestone_id") != "M2"
            or not isinstance(work_item, Mapping)
            or work_item.get("id") != M2_W6_REPAIR_WORK_ITEM_ID
        ):
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_NOT_W6_REPAIR",
                "amendment payload is not the bounded M2/W6 add-work-item repair",
            )
        amendment_digest = self._mapping_value(lineage, "amendment_digest")
        try:
            expected_amendment_digest = canonical_plan_amendment_digest(amendment)
        except Exception as exc:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_AMENDMENT_INVALID",
                "W6 amendment payload is not canonical JSON",
            ) from exc
        if not isinstance(amendment_digest, str) or amendment_digest != expected_amendment_digest:
            raise MilestoneClosureEvidenceError(
                "PLAN_LINEAGE_AMENDMENT_DIGEST_MISMATCH",
                "W6 amendment payload digest is not canonical",
            )

    def _current_plan(self):
        if (
            self.plan_binding.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
            or self.plan_binding.authority_ref != local_plan_authority_reference(self.project_id, self.plan_binding.plan_id)
            or self.governance_root.project_id != self.project_id
        ):
            raise MilestoneClosureEvidenceError("PLAN_BINDING_INVALID", "trusted Local Plan binding is unavailable")
        destination = LocalPlanAuthorityDestination(
            self.governance_root,
            self.plan_binding.plan_id,
            _plan_object_ref(self.plan_binding.plan_id),
        )
        snapshot = LocalPlanAuthorityReadAdapter(destination, binding=self.plan_binding).load()
        plan = normalize_portable_plan(snapshot.body, source_revision=snapshot.revision, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
        if str(snapshot.revision) != str(self.plan_binding.source_revision) or snapshot.digest != self.plan_binding.source_digest:
            raise MilestoneClosureEvidenceError("STALE_PLAN_BINDING", "current Plan source differs from bound Plan revision")
        return plan, tuple(plan.milestone_work_items.get("M2", ()))

    def _verify_result_lineage(self, receipt: ParentWorkAcceptanceProjection) -> None:
        record = self.execution_store.get(receipt.canonical_task_id) if self.execution_store is not None else None
        if record is None or getattr(record, "canonical_task_id", None) != receipt.canonical_task_id:
            raise MilestoneClosureEvidenceError("WORK_RESULT_MISSING", "exact Worker execution result is unavailable")
        card_raw = getattr(record, "worker_result_card", None)
        if not isinstance(card_raw, Mapping):
            raise MilestoneClosureEvidenceError("WORK_CARD_MISSING", "exact Worker Result CARD is unavailable")
        card = WorkerResultCard.from_dict(card_raw)
        task_state = getattr(getattr(record, "canonical_task_state", None), "value", getattr(record, "canonical_task_state", None))
        if (
            card.task_ref != receipt.canonical_task_id
            or card.result_handoff_ref.digest != receipt.result_digest
            or card.outcome is not ResultOutcome.SUCCESS
            or card.semantic_stop is not None
            or card.mechanical_failure is not None
            or task_state not in {"COMPLETED", "completed"}
            or card_digest_for(card.canonical_dict()) != receipt.completion_card_digest
            or receipt.completion_card_ref
            != f"execution-result-card:{receipt.canonical_task_id}#sha256:{receipt.completion_card_digest}"
            or getattr(record, "worker_result_card_digest", None) != receipt.completion_card_digest
        ):
            raise MilestoneClosureEvidenceError("WORK_CARD_MISMATCH", "receipt does not match the exact Worker Result CARD")
        opened = self.handoff_reader({"digest": receipt.result_digest}, sandbox=self.sandbox)
        envelope = opened.get("envelope")
        if (
            opened.get("digest") != receipt.result_digest
            or not isinstance(envelope, Mapping)
            or envelope.get("task_id") != receipt.canonical_task_id
            or envelope.get("project_id") != receipt.project_id
            or receipt.result_ref != f"handoff://{receipt.project_id}/{self.sandbox.worktree_id}/result/{envelope.get('artifact_id')}/{receipt.result_digest}"
        ):
            raise MilestoneClosureEvidenceError("WORK_RESULT_MISMATCH", "receipt does not match the canonical result handoff")

    def _read_parent_decision_messages(
        self,
        session_ref: str | None = None,
    ) -> dict[str, Mapping[str, Any]]:
        requested_session_ref = session_ref or self.parent_session_ref
        reader = self.parent_decision_reader
        if not callable(reader):
            raise MilestoneClosureEvidenceError(
                "PARENT_DECISION_SOURCE_UNAVAILABLE",
                "trusted exact-parent decision reader is unavailable",
            )
        try:
            raw_messages = reader(requested_session_ref)
        except Exception as exc:
            raise MilestoneClosureEvidenceError(
                "PARENT_DECISION_SOURCE_UNAVAILABLE",
                "trusted exact-parent decision history could not be read",
            ) from exc
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
            raise MilestoneClosureEvidenceError(
                "PARENT_DECISION_SOURCE_INVALID",
                "trusted exact-parent decision history has an invalid shape",
            )
        messages: dict[str, Mapping[str, Any]] = {}
        for raw in raw_messages:
            if not isinstance(raw, Mapping):
                raise MilestoneClosureEvidenceError(
                    "PARENT_DECISION_SOURCE_INVALID",
                    "trusted exact-parent history contains an invalid message",
                )
            message_id = raw.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                continue
            if message_id in messages:
                raise MilestoneClosureEvidenceError(
                    "PARENT_DECISION_SOURCE_INVALID",
                    "trusted exact-parent history contains a duplicate message id",
                )
            messages[message_id] = raw
        return messages

    @staticmethod
    def _verify_parent_decision(
        *,
        decision_ref: str,
        decision_digest: str,
        messages: Mapping[str, Mapping[str, Any]],
    ) -> None:
        match = _MESSAGE_REF_RE.fullmatch(decision_ref)
        message = messages.get(match.group(2)) if match is not None else None
        if message is None:
            raise MilestoneClosureEvidenceError(
                "PARENT_DECISION_MISSING",
                "referenced exact-parent decision message is unavailable",
            )
        if message.get("session_ref") != match.group(1):
            raise MilestoneClosureEvidenceError(
                "FOREIGN_PARENT_DECISION",
                "referenced decision message is not from the exact parent session",
            )
        if message.get("role") != "assistant" or message.get("finish") != "stop":
            raise MilestoneClosureEvidenceError(
                "PARENT_DECISION_INVALID",
                "referenced parent decision is not a completed assistant message",
            )
        text = message.get("text")
        if not isinstance(text, str) or not text:
            raise MilestoneClosureEvidenceError(
                "PARENT_DECISION_INVALID",
                "referenced parent decision has no canonical text content",
            )
        actual_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if actual_digest != decision_digest:
            raise MilestoneClosureEvidenceError(
                "EVIDENCE_DIGEST_MISMATCH",
                "parent decision message digest differs from the acceptance receipt",
            )

    def _w2_production_evidence(self, stored_proof: Mapping[str, Any] | None = None) -> dict[str, Any]:
        resource_id = self.production_runtime_id
        if resource_id != M2_W2_PRODUCTION_RUNTIME_ID:
            raise MilestoneClosureEvidenceError("PRODUCTION_RESOURCE_MISMATCH", "W2 runtime resource id differs")
        receipt_digest = self._resolve_w2_receipt_digest()
        if stored_proof is not None:
            if not self._valid_production_provenance(stored_proof):
                raise MilestoneClosureEvidenceError("PRODUCTION_PROOF_INVALID", "supplied W2 proof is not a valid existing adapter projection")
            return {"receipt_digest": receipt_digest, "runtime_provenance": dict(stored_proof)}
        try:
            provenance = host_resources.evaluate_runtime_provenance_ref(resource_id, self.project_root, self.host_config)
        except Exception as exc:
            raise MilestoneClosureEvidenceError("PRODUCTION_RECEIPT_MISSING", "trusted W2 runtime receipt could not be resolved") from exc
        if not isinstance(provenance, Mapping):
            raise MilestoneClosureEvidenceError("PRODUCTION_RECEIPT_MISSING", "trusted W2 runtime proof is unavailable")
        if not self._valid_production_provenance(provenance):
            raise MilestoneClosureEvidenceError("PRODUCTION_PROOF_INVALID", "existing W2 production adapter did not prove parity and active eligibility")
        return {"receipt_digest": receipt_digest, "runtime_provenance": dict(provenance)}

    def _resolve_w2_receipt_digest(self) -> str:
        resource_id = self.production_runtime_id
        try:
            receipt_ref = host_resources.deployment_receipt_reference_ref(resource_id, self.host_config)
        except Exception as exc:
            raise MilestoneClosureEvidenceError("PRODUCTION_RECEIPT_MISSING", "trusted W2 runtime receipt could not be resolved") from exc
        projection = receipt_ref.get("runtime_provenance_ref") if isinstance(receipt_ref, Mapping) else None
        if not isinstance(projection, Mapping) or projection.get("resource_id") != resource_id:
            raise MilestoneClosureEvidenceError("PRODUCTION_RECEIPT_MISMATCH", "logical W2 receipt reference is foreign")
        digest = projection.get("sha256")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise MilestoneClosureEvidenceError("PRODUCTION_RECEIPT_INVALID", "trusted W2 receipt digest is invalid")
        return digest

    @staticmethod
    def _valid_production_provenance(provenance: Mapping[str, Any]) -> bool:
        fingerprints = provenance.get("fingerprints")
        eligibility = provenance.get("eligibility")
        runtime_subject = provenance.get("runtime_subject")
        capability = eligibility.get("capability_parity") if isinstance(eligibility, Mapping) else None
        stages = ("source_fingerprint", "configured_fingerprint", "deployed_fingerprint", "running_fingerprint")
        return bool(
            isinstance(fingerprints, Mapping)
            and all(isinstance(fingerprints.get(stage), str) and fingerprints.get(stage) for stage in stages)
            and len({fingerprints.get(stage) for stage in stages}) == 1
            and isinstance(eligibility, Mapping)
            and eligibility.get("provenance_valid") is True
            and eligibility.get("eligible") is True
            and isinstance(capability, Mapping)
            and capability.get("parity") is True
            and isinstance(runtime_subject, Mapping)
        )

    @classmethod
    def _valid_stored_w2_provenance(cls, receipt: ParentWorkAcceptanceProjection) -> bool:
        if not isinstance(receipt.production_provenance, Mapping):
            return False
        expected = hashlib.sha256(canonical_json(dict(receipt.production_provenance)).encode("utf-8")).hexdigest()
        return (
            receipt.production_provenance_digest == expected
            and cls._valid_production_provenance(receipt.production_provenance)
        )


def _plan_object_ref(plan_id: str):
    from aota_forge.core.identity.ids import make_id
    from aota_forge.core.identity.kinds import IdKind, SubjectKind
    from aota_forge.core.identity.refs import object_ref_subject

    return object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN))


__all__ = [
    "CURRENT_INSPECTOR_SESSION_IS_ACCEPTANCE_AUTHORITY",
    "GENERALIZED_PLAN_REVISION_ENGINE_CREATED",
    "HISTORICAL_WORK_ACCEPTANCE_RECONSTRUCTION",
    "LEGACY_COORDINATOR_REQUIRED",
    "LATEST_BY_TIME_SELECTION_ALLOWED",
    "M2_HISTORICAL_WORK_ITEMS",
    "M2_W6_REPAIR_WORK_ITEM_ID",
    "NEW_WORK_LEDGER_CREATED",
    "W6_SAME_PLAN_PREDECESSOR_REVISION_EXCEPTION_ONLY",
    "PARENT_WORK_ACCEPTANCE_IS_AUTHORITY",
    "PARENT_WORK_ACCEPTANCE_IS_MUTABLE_STATE",
    "FileBackedParentWorkAcceptanceProjectionStore",
    "MilestoneClosureEvidenceError",
    "ParentWorkAcceptanceProjection",
    "ThinMilestoneClosureEvidence",
    "ThinMilestoneClosureEvidenceAdapter",
]
