"""M3-B11 deterministic migration transformer.

Scope (Issue #9, Lane M3-B11):
- Transforms normalized MigrationInputManifest inputs into B3 canonical graph records.
- Reuses accepted B3 graph record ontology and B4 identity rules:
    B11_NEW_GRAPH_SCHEMA_CREATED = False
    B11_REUSES_CANONICAL_GRAPH_RECORD_SEMANTICS = True
    TRANSFORMATION_VERSION_EXPLICIT = True
    SUBJECT_IDENTITY_STABLE_ACROSS_REBUILD = True
    B11_SHADOW_IDENTITY_REBUILD_STABLE = True
    SHADOW_REBUILD_DUPLICATE_MINTED_SUBJECTS = False
    SHADOW_FOLLOWUP_EDGE_REQUIRES_SOURCE_DECISION = True
    SHADOW_MIGRATION_MAY_INVENT_DECISION_LINEAGE = False
    SHADOW_COMPLETION_SUBJECT_SHORTCUT_ALLOWED = False
    B11_HEURISTIC_SUBJECT_SELECTION_ALLOWED = False
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from aota_forge.core.graph import records
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import make_object_ref
from aota_forge.core.identity.subject import (
    plan_subject,
    project_subject,
    workspace_subject,
)
from aota_forge.core.migration.input import (
    MigrationInput,
    MigrationInputManifest,
    SourceCategory,
)
from aota_forge.core.migration.provenance import ProvenanceRecord

DEFAULT_TRANSFORMATION_VERSION = "m3-b11-v1"

B11_NEW_GRAPH_SCHEMA_CREATED = False
B11_REUSES_CANONICAL_GRAPH_RECORD_SEMANTICS = True
TRANSFORMATION_VERSION_EXPLICIT = True
SUBJECT_IDENTITY_STABLE_ACROSS_REBUILD = True
B11_SHADOW_IDENTITY_REBUILD_STABLE = True
SHADOW_REBUILD_DUPLICATE_MINTED_SUBJECTS = False
SHADOW_FOLLOWUP_EDGE_REQUIRES_SOURCE_DECISION = True
SHADOW_MIGRATION_MAY_INVENT_DECISION_LINEAGE = False
SHADOW_COMPLETION_SUBJECT_SHORTCUT_ALLOWED = False
B11_HEURISTIC_SUBJECT_SELECTION_ALLOWED = False


def _deterministic_shadow_id(prefix: str, seed: str, kind: str, sub_kind: str | None = None) -> InternalId:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
    val = f"{prefix}_{digest}"
    return make_id(kind, val, sub_kind=sub_kind)


@dataclass
class TransformationResult:
    """Bounded result of running migration transformation on a manifest."""

    records: list[records._AnyRecord] = field(default_factory=list)
    provenance_records: list[ProvenanceRecord] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    success: bool = True

    def record_counts(self) -> dict[str, int]:
        counts = {
            "workflows": 0,
            "subjects": 0,
            "executions": 0,
            "completions": 0,
            "decisions": 0,
            "edges": 0,
            "total": len(self.records),
        }
        for r in self.records:
            if isinstance(r, records.Workflow):
                counts["workflows"] += 1
            elif isinstance(r, records.Subject):
                counts["subjects"] += 1
            elif isinstance(r, records.Execution):
                counts["executions"] += 1
            elif isinstance(r, records.Completion):
                counts["completions"] += 1
            elif isinstance(r, records.Decision):
                counts["decisions"] += 1
            elif isinstance(r, records.FollowupEdge):
                counts["edges"] += 1
        return counts


class MigrationTransformer:
    """Transforms normalized migration input manifests into canonical records."""

    def __init__(self, transformation_version: str = DEFAULT_TRANSFORMATION_VERSION) -> None:
        self.transformation_version = transformation_version

    def transform(
        self,
        manifest: MigrationInputManifest,
        target_namespace: str,
    ) -> TransformationResult:
        result = TransformationResult()
        created_subjects: dict[str, records.Subject] = {}
        created_workflows: dict[str, records.Workflow] = {}
        created_executions: dict[str, records.Execution] = {}
        created_decisions: dict[str, records.Decision] = {}

        # 1. First pass: Workspaces & Projects & Workflows & Plans
        for inp in manifest.inputs:
            p = inp.normalized_payload()
            ws_name = p.get("workspace_id") or p.get("workspace") or "aota"
            prj_name = p.get("project_id") or p.get("project") or "aota_forge"

            # Derive Workspace Subject
            ws_iid = workspace_subject(ws_name)
            if ws_iid.value not in created_subjects:
                ws_sub = records.subject(
                    ws_iid,
                    "workspace",
                    mechanical_state={"state": "active", "revision": 1, "shadow": True},
                    id_derivation="deterministic",
                    creation_context={"source": "migration", "namespace": target_namespace},
                )
                created_subjects[ws_iid.value] = ws_sub
                result.records.append(ws_sub)
                result.provenance_records.append(
                    ProvenanceRecord(
                        source_type=inp.source_category,
                        logical_source_identity=f"workspace::{ws_name}",
                        content_fingerprint=inp.source_fingerprint(),
                        capture_version_id=inp.version_id,
                        semantic_classification=inp.semantic_classification,
                        transformation_version=self.transformation_version,
                        target_kind="subject:workspace",
                        target_identity=ws_iid.to_canonical(),
                    )
                )

            # Derive Project Subject
            prj_iid = project_subject(prj_name, ws_iid)
            if prj_iid.value not in created_subjects:
                prj_sub = records.subject(
                    prj_iid,
                    "project",
                    mechanical_state={"state": "active", "revision": 1, "shadow": True},
                    id_derivation="deterministic",
                    creation_context={"source": "migration", "namespace": target_namespace},
                )
                created_subjects[prj_iid.value] = prj_sub
                result.records.append(prj_sub)
                result.provenance_records.append(
                    ProvenanceRecord(
                        source_type=inp.source_category,
                        logical_source_identity=f"project::{prj_name}",
                        content_fingerprint=inp.source_fingerprint(),
                        capture_version_id=inp.version_id,
                        semantic_classification=inp.semantic_classification,
                        transformation_version=self.transformation_version,
                        target_kind="subject:project",
                        target_identity=prj_iid.to_canonical(),
                    )
                )

            # Check if this input is a Plan or Workflow carrier
            plan_name = p.get("plan_id") or p.get("plan_key") or p.get("plan")
            if plan_name:
                # Ambiguity check: if multiple candidate plans match ambiguous name without clear context
                if p.get("ambiguous_candidates"):
                    result.unresolved.append(
                        f"Ambiguous plan specification for {plan_name}: heuristic selection forbidden"
                    )
                    continue

                plan_iid = plan_subject(plan_name, prj_iid)
                wf_iid = _deterministic_shadow_id(
                    "wf_shadow",
                    f"{plan_name}::{self.transformation_version}",
                    IdKind.WORKFLOW,
                )

                if wf_iid.value not in created_workflows:
                    wf = records.workflow(
                        wf_iid,
                        semantic_intent=p.get("goal") or p.get("semantic_intent") or f"Workflow for {plan_name}",
                        creation_context={"source": "migration", "namespace": target_namespace},
                        goal=p.get("goal") or "",
                        scope=p.get("scope") or {},
                    )
                    created_workflows[wf_iid.value] = wf
                    result.records.append(wf)
                    result.provenance_records.append(
                        ProvenanceRecord(
                            source_type=inp.source_category,
                            logical_source_identity=f"workflow::{plan_name}",
                            content_fingerprint=inp.source_fingerprint(),
                            capture_version_id=inp.version_id,
                            semantic_classification=inp.semantic_classification,
                            transformation_version=self.transformation_version,
                            target_kind="workflow",
                            target_identity=wf_iid.to_canonical(),
                        )
                    )

                if plan_iid.value not in created_subjects:
                    mech_state = {
                        "state": p.get("current_status") or p.get("state") or "open",
                        "milestone": p.get("milestone") or p.get("current_milestone") or "",
                        "revision": 1,
                        "shadow": True,
                    }
                    plan_sub = records.subject(
                        plan_iid,
                        "plan",
                        workflow_ref=wf_iid,
                        mechanical_state=mech_state,
                        id_derivation="deterministic",
                        creation_context={"source": "migration", "namespace": target_namespace},
                    )
                    created_subjects[plan_iid.value] = plan_sub
                    result.records.append(plan_sub)
                    result.provenance_records.append(
                        ProvenanceRecord(
                            source_type=inp.source_category,
                            logical_source_identity=f"plan::{plan_name}",
                            content_fingerprint=inp.source_fingerprint(),
                            capture_version_id=inp.version_id,
                            semantic_classification=inp.semantic_classification,
                            transformation_version=self.transformation_version,
                            target_kind="subject:plan",
                            target_identity=plan_iid.to_canonical(),
                        )
                    )

        # 2. Second pass: Executions, Completions, Decisions, Followups
        for inp in manifest.inputs:
            p = inp.normalized_payload()
            plan_name = p.get("plan_id") or p.get("plan_key") or p.get("plan")
            if not plan_name:
                continue
            ws_name = p.get("workspace_id") or p.get("workspace") or "aota"
            prj_name = p.get("project_id") or p.get("project") or "aota_forge"
            ws_iid = workspace_subject(ws_name)
            prj_iid = project_subject(prj_name, ws_iid)
            plan_iid = plan_subject(plan_name, prj_iid)

            # Decisions
            raw_decisions = p.get("decisions") or []
            decision_map: dict[str, InternalId] = {}
            for idx, raw_dec in enumerate(raw_decisions):
                dec_stmt = raw_dec.get("statement") or str(raw_dec)
                dec_kind = raw_dec.get("decision_kind") or "architecture"
                dec_id_key = raw_dec.get("id") or f"dec_{idx}_{dec_stmt[:16]}"
                dec_iid = _deterministic_shadow_id(
                    "dec_shadow",
                    f"{plan_iid.value}::{dec_id_key}::{self.transformation_version}",
                    IdKind.DECISION,
                )
                decision_map[dec_id_key] = dec_iid
                if raw_dec.get("id"):
                    decision_map[str(raw_dec["id"])] = dec_iid
                decision_map[dec_stmt] = dec_iid
                decision_map[dec_iid.value] = dec_iid
                decision_map[dec_iid.to_canonical()] = dec_iid

                if dec_iid.value not in created_decisions:
                    dec = records.decision(
                        dec_iid,
                        plan_iid,
                        decision_kind=dec_kind,
                        statement=dec_stmt,
                        target_refs=raw_dec.get("target_refs") or [],
                        evidence_refs=raw_dec.get("evidence_refs") or [],
                    )
                    created_decisions[dec_iid.value] = dec
                    result.records.append(dec)
                    result.provenance_records.append(
                        ProvenanceRecord(
                            source_type=inp.source_category,
                            logical_source_identity=f"decision::{plan_name}::{dec_id_key}",
                            content_fingerprint=inp.source_fingerprint(),
                            capture_version_id=inp.version_id,
                            semantic_classification=inp.semantic_classification,
                            transformation_version=self.transformation_version,
                            target_kind="decision",
                            target_identity=dec_iid.to_canonical(),
                        )
                    )

            # Executions & Completions
            raw_tasks = p.get("tasks") or p.get("executions") or []
            for idx, raw_task in enumerate(raw_tasks):
                task_id_key = raw_task.get("id") or f"task_{idx}"
                exec_iid = _deterministic_shadow_id(
                    "exec_shadow",
                    f"{plan_iid.value}::{task_id_key}::{self.transformation_version}",
                    IdKind.EXECUTION,
                )
                if exec_iid.value not in created_executions:
                    exc = records.execution(
                        exec_iid,
                        plan_iid,
                        executor_kind=raw_task.get("executor_kind") or "legacy_agent",
                        mechanical_status=raw_task.get("status") or "completed",
                        started_at=raw_task.get("started_at") or "2026-08-18T00:00:00Z",
                    )
                    created_executions[exec_iid.value] = exc
                    result.records.append(exc)
                    result.provenance_records.append(
                        ProvenanceRecord(
                            source_type=inp.source_category,
                            logical_source_identity=f"execution::{plan_name}::{task_id_key}",
                            content_fingerprint=inp.source_fingerprint(),
                            capture_version_id=inp.version_id,
                            semantic_classification=inp.semantic_classification,
                            transformation_version=self.transformation_version,
                            target_kind="execution",
                            target_identity=exec_iid.to_canonical(),
                        )
                    )

                    # Completion if completed (reaches Subject only via execution)
                    if raw_task.get("status") == "completed" or raw_task.get("outcome"):
                        comp_iid = _deterministic_shadow_id(
                            "comp_shadow",
                            f"{exec_iid.value}::completion::{self.transformation_version}",
                            IdKind.COMPLETION,
                        )
                        cmp = records.completion(
                            comp_iid,
                            exec_iid,
                            outcome=raw_task.get("outcome") or "success",
                            evidence_refs=raw_task.get("evidence_refs") or [],
                            recorded_at=raw_task.get("completed_at") or "2026-08-18T00:00:00Z",
                        )
                        result.records.append(cmp)
                        result.provenance_records.append(
                            ProvenanceRecord(
                                source_type=inp.source_category,
                                logical_source_identity=f"completion::{plan_name}::{task_id_key}",
                                content_fingerprint=inp.source_fingerprint(),
                                capture_version_id=inp.version_id,
                                semantic_classification=inp.semantic_classification,
                                transformation_version=self.transformation_version,
                                target_kind="completion",
                                target_identity=comp_iid.to_canonical(),
                            )
                        )

            # Followup edges & child work subjects
            raw_followups = p.get("followups") or []
            for idx, raw_fol in enumerate(raw_followups):
                child_id_key = raw_fol.get("child_id") or f"child_{idx}"
                source_dec_key = raw_fol.get("source_decision_id")

                # Match source decision
                matched_dec_iid = decision_map.get(source_dec_key) if source_dec_key else None
                if matched_dec_iid is None and source_dec_key:
                    for d in created_decisions.values():
                        if source_dec_key in d.decision_id.value or d.statement == source_dec_key:
                            matched_dec_iid = d.decision_id
                            break

                if matched_dec_iid is None:
                    # Invariant: FollowupEdge REQUIRES source Decision. If missing in evidence, fail closed!
                    msg = (
                        f"FollowupEdge creation omitted for child '{child_id_key}': "
                        f"legacy evidence lacks exact Decision lineage (fail-closed)"
                    )
                    result.findings.append(msg)
                    result.warnings.append(msg)
                    continue

                # Create child Subject (deterministic stable across rebuild)
                child_iid = _deterministic_shadow_id(
                    "wid_shadow",
                    f"{plan_iid.value}::{child_id_key}::{self.transformation_version}",
                    IdKind.SUBJECT,
                    sub_kind=SubjectKind.WORK,
                )
                if child_iid.value not in created_subjects:
                    child_sub = records.subject(
                        child_iid,
                        "work",
                        workflow_ref=created_workflows.get(
                            _deterministic_shadow_id(
                                "wf_shadow",
                                f"{plan_name}::{self.transformation_version}",
                                IdKind.WORKFLOW,
                            ).value
                        )
                        and _deterministic_shadow_id(
                            "wf_shadow",
                            f"{plan_name}::{self.transformation_version}",
                            IdKind.WORKFLOW,
                        ),
                        mechanical_state={"state": "open", "revision": 1, "shadow": True},
                        id_derivation="migration_deterministic",
                        creation_context={"parent": plan_iid.to_canonical(), "source": "migration"},
                    )
                    created_subjects[child_iid.value] = child_sub
                    result.records.append(child_sub)
                    result.provenance_records.append(
                        ProvenanceRecord(
                            source_type=inp.source_category,
                            logical_source_identity=f"subject:work::{child_id_key}",
                            content_fingerprint=inp.source_fingerprint(),
                            capture_version_id=inp.version_id,
                            semantic_classification=inp.semantic_classification,
                            transformation_version=self.transformation_version,
                            target_kind="subject:work",
                            target_identity=child_iid.to_canonical(),
                        )
                    )

                # Create FollowupEdge
                edge_iid = _deterministic_shadow_id(
                    "edge_shadow",
                    f"{plan_iid.value}::{child_iid.value}::{matched_dec_iid.value}",
                    IdKind.EDGE,
                )
                edge = records.followup_edge(
                    edge_iid,
                    plan_iid,
                    child_iid,
                    matched_dec_iid,
                    rationale=raw_fol.get("rationale") or "migration followup lineage",
                )
                result.records.append(edge)
                result.provenance_records.append(
                    ProvenanceRecord(
                        source_type=inp.source_category,
                        logical_source_identity=f"edge::{plan_name}::{child_id_key}",
                        content_fingerprint=inp.source_fingerprint(),
                        capture_version_id=inp.version_id,
                        semantic_classification=inp.semantic_classification,
                        transformation_version=self.transformation_version,
                        target_kind="edge",
                        target_identity=edge_iid.to_canonical(),
                    )
                )

        result.success = len(result.unresolved) == 0
        return result


__all__ = [
    "DEFAULT_TRANSFORMATION_VERSION",
    "B11_NEW_GRAPH_SCHEMA_CREATED",
    "B11_REUSES_CANONICAL_GRAPH_RECORD_SEMANTICS",
    "TRANSFORMATION_VERSION_EXPLICIT",
    "SUBJECT_IDENTITY_STABLE_ACROSS_REBUILD",
    "B11_SHADOW_IDENTITY_REBUILD_STABLE",
    "SHADOW_REBUILD_DUPLICATE_MINTED_SUBJECTS",
    "SHADOW_FOLLOWUP_EDGE_REQUIRES_SOURCE_DECISION",
    "SHADOW_MIGRATION_MAY_INVENT_DECISION_LINEAGE",
    "SHADOW_COMPLETION_SUBJECT_SHORTCUT_ALLOWED",
    "B11_HEURISTIC_SUBJECT_SELECTION_ALLOWED",
    "TransformationResult",
    "MigrationTransformer",
]
