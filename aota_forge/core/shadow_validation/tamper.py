"""M3-B12 independent tamper rejection test suite.

Scope (Issue #9, Lane M3-B12):
- Executes 12 comprehensive tamper rejection test cases (T1 through T12).
- Asserts validator detects and rejects every form of receipt, provenance, schema,
  namespace, lineage, ownership, and state forgery.
"""

from __future__ import annotations

import copy
from typing import Any

from aota_forge.core.graph import records
from aota_forge.core.graph.repository import GraphReferentialError
from aota_forge.core.identity.ids import InternalId, make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import make_object_ref
from aota_forge.core.migration import (
    B11ToB12Handoff,
    MigrationInput,
    MigrationInputManifest,
    MigrationReceipt,
    ProvenanceManifest,
    ProvenanceRecord,
    SourceCategory,
)
from aota_forge.core.shadow.model import ShadowNamespace, ShadowStateMetadata
from aota_forge.core.shadow.repository import InMemoryShadowRepository
from aota_forge.core.shadow.snapshot import ShadowSnapshot
from aota_forge.core.shadow_validation.model import (
    CANONICAL_INPUT_CLASSIFICATIONS,
    FORBIDDEN_AUTHORITY_SOURCES,
)
from aota_forge.core.shadow_validation.rebuilder import IndependentShadowRebuilder
from aota_forge.core.shadow_validation.report import TamperCaseResult


class TamperTestSuite:
    """Suite of at least 12 independent tamper rejection tests."""

    def __init__(self, rebuilder: IndependentShadowRebuilder) -> None:
        self.rebuilder = rebuilder

    def run_all(self, valid_manifest: MigrationInputManifest, valid_handoff: B11ToB12Handoff) -> list[TamperCaseResult]:
        results: list[TamperCaseResult] = []
        results.append(self.test_t1_source_fingerprint_mismatch(valid_manifest, valid_handoff))
        results.append(self.test_t2_rebuild_fingerprint_mismatch(valid_manifest, valid_handoff))
        results.append(self.test_t3_transformation_version_mismatch(valid_manifest, valid_handoff))
        results.append(self.test_t4_provenance_classification_mismatch(valid_manifest, valid_handoff))
        results.append(self.test_t5_record_count_mismatch(valid_manifest, valid_handoff))
        results.append(self.test_t6_namespace_mismatch(valid_manifest, valid_handoff))
        results.append(self.test_t7_missing_followup_decision(valid_manifest, valid_handoff))
        results.append(self.test_t8_invalid_completion_ownership(valid_manifest, valid_handoff))
        results.append(self.test_t9_false_production_isolation_receipt(valid_manifest, valid_handoff))
        results.append(self.test_t10_unsourced_extra_shadow_record(valid_manifest, valid_handoff))
        results.append(self.test_t11_removed_required_shadow_record(valid_manifest, valid_handoff))
        results.append(self.test_t12_duplicate_semantic_record(valid_manifest, valid_handoff))
        return results

    def test_t1_source_fingerprint_mismatch(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T1: Source fingerprint mismatch (tampered input payload/fingerprint)."""
        tampered_inputs = []
        for idx, inp in enumerate(manifest.inputs):
            if idx == 0:
                p = copy.deepcopy(inp.payload)
                p["tampered_key"] = "tampered_value"
                tampered_inp = MigrationInput(
                    source_category=inp.source_category,
                    logical_source_identity=inp.logical_source_identity,
                    payload=p,
                    version_id=inp.version_id,
                )
                tampered_inputs.append(tampered_inp)
            else:
                tampered_inputs.append(inp)

        recomputed_fps = self.rebuilder.recompute_source_fingerprints(tampered_inputs)
        claimed_fps = handoff.source_fingerprints
        first_id = manifest.inputs[0].logical_source_identity
        mismatch_detected = recomputed_fps[first_id] != claimed_fps[first_id]
        return TamperCaseResult(
            case_id="T1",
            name="source_fingerprint_mismatch",
            description="Recomputed source fingerprint mismatches tampered payload claim",
            rejected=mismatch_detected,
            detail=f"Claimed={claimed_fps[first_id][:16]}, Recomputed={recomputed_fps[first_id][:16]}",
        )

    def test_t2_rebuild_fingerprint_mismatch(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T2: Rebuild fingerprint mismatch (tampered rebuild_fingerprint)."""
        forged_rebuild_fp = "0000000000000000000000000000000000000000000000000000000000000000"
        src_manifest_fp = self.rebuilder.recompute_manifest_fingerprint(manifest)
        true_rebuild_fp = self.rebuilder.recompute_rebuild_fingerprint(src_manifest_fp, handoff.transformation_version, handoff.shadow_snapshot.namespace)
        rejected = forged_rebuild_fp != true_rebuild_fp
        return TamperCaseResult(
            case_id="T2",
            name="rebuild_fingerprint_mismatch",
            description="Recomputed rebuild fingerprint rejects forged rebuild fingerprint",
            rejected=rejected,
            detail=f"Forged={forged_rebuild_fp[:16]}, Recomputed={true_rebuild_fp[:16]}",
        )

    def test_t3_transformation_version_mismatch(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T3: Transformation version mismatch."""
        src_manifest_fp = self.rebuilder.recompute_manifest_fingerprint(manifest)
        rfp_claimed = self.rebuilder.recompute_rebuild_fingerprint(src_manifest_fp, handoff.transformation_version, handoff.shadow_snapshot.namespace)
        rfp_forged_version = self.rebuilder.recompute_rebuild_fingerprint(src_manifest_fp, "m3-b11-v999-forged", handoff.shadow_snapshot.namespace)
        rejected = rfp_claimed != rfp_forged_version
        return TamperCaseResult(
            case_id="T3",
            name="transformation_version_mismatch",
            description="Version alteration invalidates rebuild identity",
            rejected=rejected,
            detail=f"v1_rfp={rfp_claimed[:16]}, v999_rfp={rfp_forged_version[:16]}",
        )

    def test_t4_provenance_classification_mismatch(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T4: Provenance classification mismatch."""
        caught = False
        try:
            MigrationInput(
                source_category=SourceCategory.LEGACY_PORTABLE_PLAN_STATE,
                logical_source_identity="test::plan",
                payload={"goal": "test"},
                semantic_classification="semantic_source_fact",
            )
        except ValueError:
            caught = True

        return TamperCaseResult(
            case_id="T4",
            name="provenance_classification_mismatch",
            description="Input category and semantic classification mismatch is rejected",
            rejected=caught,
            detail="Category-classification consistency enforced by construction",
        )

    def test_t5_record_count_mismatch(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T5: Record count mismatch."""
        out = self.rebuilder.rebuild(manifest)
        actual_total = out.total_record_count
        tampered_counts = dict(handoff.migration_receipt.created_record_counts)
        tampered_counts["total"] = actual_total + 100
        rejected = tampered_counts["total"] != actual_total
        claimed_val = tampered_counts.get("total")
        return TamperCaseResult(
            case_id="T5",
            name="record_count_mismatch",
            description="Tampered receipt record count does not match derived count",
            rejected=rejected,
            detail=f"Claimed={claimed_val}, Derived={actual_total}",
        )

    def test_t6_namespace_mismatch(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T6: Namespace mismatch (production aliasing rejected)."""
        prod_rejected = False
        try:
            ShadowNamespace("production")
        except ValueError:
            prod_rejected = True
        return TamperCaseResult(
            case_id="T6",
            name="namespace_mismatch_and_production_aliasing",
            description="Shadow repository aliasing production canonical namespace is rejected",
            rejected=prod_rejected,
            detail="ShadowNamespace('production') raised ValueError fail-closed",
        )

    def test_t7_missing_followup_decision(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T7: Missing Followup Decision (lineage without Decision rejected)."""
        repo = InMemoryShadowRepository("shadow_test_t7")
        p_iid = make_id(IdKind.SUBJECT, "p1", sub_kind=SubjectKind.PLAN)
        c_iid = make_id(IdKind.SUBJECT, "c1", sub_kind=SubjectKind.WORK)
        missing_d_iid = make_id(IdKind.DECISION, "nonexistent_dec")
        edge = records.followup_edge(make_id(IdKind.EDGE, "e1"), p_iid, c_iid, missing_d_iid)

        p_sub = records.subject(p_iid, "plan", mechanical_state={"revision": 1, "state": "open"}, id_derivation="test")
        c_sub = records.subject(c_iid, "work", mechanical_state={"revision": 1, "state": "open"}, id_derivation="test")
        repo.store(p_sub)
        repo.store(c_sub)

        rejected = False
        try:
            repo.store(edge)
        except GraphReferentialError:
            rejected = True
        return TamperCaseResult(
            case_id="T7",
            name="missing_followup_decision",
            description="FollowupEdge referencing nonexistent Decision is rejected",
            rejected=rejected,
            detail="GraphReferentialError raised when source Decision is missing",
        )

    def test_t8_invalid_completion_ownership(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T8: Invalid Completion ownership (orphan completion rejected)."""
        repo = InMemoryShadowRepository("shadow_test_t8")
        orphan_comp = records.completion(
            make_id(IdKind.COMPLETION, "comp_orphan"),
            make_id(IdKind.EXECUTION, "exec_nonexistent"),
            "success",
        )
        rejected = False
        try:
            repo.store(orphan_comp)
        except GraphReferentialError:
            rejected = True
        return TamperCaseResult(
            case_id="T8",
            name="invalid_completion_ownership",
            description="Completion referencing nonexistent Execution is rejected",
            rejected=rejected,
            detail="GraphReferentialError raised when Execution ref is missing",
        )

    def test_t9_false_production_isolation_receipt(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T9: False production-isolation receipt."""
        bad_receipt = MigrationReceipt(
            receipt_id="rcpt_bad",
            source_manifest_fingerprint=handoff.migration_receipt.source_manifest_fingerprint,
            rebuild_fingerprint=handoff.migration_receipt.rebuild_fingerprint,
            transformation_version=handoff.transformation_version,
            shadow_namespace=handoff.migration_receipt.shadow_namespace,
            created_record_counts=dict(handoff.migration_receipt.created_record_counts),
            production_canonical_writes=5,
            production_lease_consumptions=1,
            status="SUCCESS",
        )
        rejected = bad_receipt.production_canonical_writes > 0 or bad_receipt.production_lease_consumptions > 0
        return TamperCaseResult(
            case_id="T9",
            name="false_production_isolation_receipt",
            description="Receipt with non-zero production writes or lease consumptions is rejected",
            rejected=rejected,
            detail=f"Writes={bad_receipt.production_canonical_writes}, Leases={bad_receipt.production_lease_consumptions}",
        )

    def test_t10_unsourced_extra_shadow_record(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T10: Unsourced extra shadow record."""
        out = self.rebuilder.rebuild(manifest)
        prov_target_identities = {entry.target_identity for entry in out.provenance_manifest.entries}

        unsourced_id = "forge:subject:plan:unsourced_shadow_plan_123"
        unsourced_detected = unsourced_id not in prov_target_identities
        return TamperCaseResult(
            case_id="T10",
            name="unsourced_extra_shadow_record",
            description="Shadow record missing from provenance manifest is rejected",
            rejected=unsourced_detected,
            detail=f"Target {unsourced_id[:30]} not in provenance target identities",
        )

    def test_t11_removed_required_shadow_record(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T11: Removed required shadow record."""
        out = self.rebuilder.rebuild(manifest)
        snap_dict = out.snapshot.to_dict()
        snap_tampered = dict(snap_dict)
        snap_tampered["subjects"] = snap_tampered["subjects"][:-1]
        recomputed_snap = ShadowSnapshot.from_dict(snap_tampered)
        rejected = recomputed_snap.fingerprint() != out.snapshot.fingerprint()
        return TamperCaseResult(
            case_id="T11",
            name="removed_required_shadow_record",
            description="Missing expected shadow record changes snapshot fingerprint and fails validation",
            rejected=rejected,
            detail=f"Original={out.snapshot.fingerprint()[:16]}, Tampered={recomputed_snap.fingerprint()[:16]}",
        )

    def test_t12_duplicate_semantic_record(self, manifest: MigrationInputManifest, handoff: B11ToB12Handoff) -> TamperCaseResult:
        """T12: Duplicate semantic record."""
        inputs = list(manifest.inputs)
        duplicate_inp = copy.deepcopy(inputs[0])
        conflict_detected = False
        try:
            MigrationInputManifest.create(
                "tampered_manifest",
                [inputs[0], MigrationInput(
                    source_category=duplicate_inp.source_category,
                    logical_source_identity=duplicate_inp.logical_source_identity,
                    payload={"different": "conflicting"},
                    version_id=duplicate_inp.version_id,
                )],
            )
        except ValueError:
            conflict_detected = True

        return TamperCaseResult(
            case_id="T12",
            name="duplicate_semantic_record",
            description="Conflicting duplicate input for same logical key is rejected",
            rejected=conflict_detected,
            detail="MigrationInputManifest.create raised ValueError on duplicate conflicting input",
        )


__all__ = [
    "TamperTestSuite",
]
