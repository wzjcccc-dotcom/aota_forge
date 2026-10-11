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
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.core.plan.validation import is_plan_id
from aota_forge.core.project.manifest import PROJECT_ID_RE
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
        if plan.current_milestone != "M2" or work_item_id not in expected_work:
            raise MilestoneClosureEvidenceError("WORK_IDENTITY_MISMATCH", "Work item is not in the bound current Milestone")
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
        if plan.current_milestone != "M2":
            reasons.append("bound Plan current milestone is not M2")
        receipts = self.receipt_store.list_for_milestone(self.project_id, self.plan_binding.plan_id, "M2")
        selected: dict[str, ParentWorkAcceptanceProjection] = {}
        for receipt in receipts:
            if receipt.parent_session_ref != self.parent_session_ref:
                reasons.append(f"foreign parent session acceptance: {receipt.work_item_id}")
                continue
            if receipt.plan_source_revision != str(self.plan_binding.source_revision):
                reasons.append(f"stale Plan source revision acceptance: {receipt.work_item_id}")
                continue
            if receipt.work_item_id not in work_items:
                reasons.append(f"foreign Work item acceptance: {receipt.work_item_id}")
                continue
            expected_task_prefix = f"{self.project_id}:{self.plan_binding.plan_id}:M2:{receipt.work_item_id}:"
            if not receipt.canonical_task_id.startswith(expected_task_prefix):
                reasons.append(f"foreign canonical task acceptance: {receipt.work_item_id}")
                continue
            if receipt.work_item_id in selected:
                reasons.append(f"ambiguous accepted lineage for {receipt.work_item_id}")
                continue
            try:
                self._verify_result_lineage(receipt)
            except MilestoneClosureEvidenceError as exc:
                reasons.append(f"{receipt.work_item_id}: {exc.code}")
                continue
            selected[receipt.work_item_id] = receipt
        missing = sorted(set(work_items) - set(selected))
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
    "LEGACY_COORDINATOR_REQUIRED",
    "LATEST_BY_TIME_SELECTION_ALLOWED",
    "NEW_WORK_LEDGER_CREATED",
    "PARENT_WORK_ACCEPTANCE_IS_AUTHORITY",
    "PARENT_WORK_ACCEPTANCE_IS_MUTABLE_STATE",
    "FileBackedParentWorkAcceptanceProjectionStore",
    "MilestoneClosureEvidenceError",
    "ParentWorkAcceptanceProjection",
    "ThinMilestoneClosureEvidence",
    "ThinMilestoneClosureEvidenceAdapter",
]
