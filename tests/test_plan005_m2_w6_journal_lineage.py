from __future__ import annotations

import hashlib
import importlib.util
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from aota_forge.adapters.plan_authority.binding import PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE, PlanAuthorityBinding
from aota_forge.adapters.plan_authority.local_governance import LocalPlanAuthorityDestination, local_plan_authority_reference
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import PORTABLE_PLAN_SOURCE_LOCAL
from aota_forge.governance import milestone_closure_evidence as closure
from aota_forge.governance.plan_semantics import (
    M2_RV2_WAIVER_DECISION_REF,
    add_work_item_to_plan,
    canonical_plan_amendment_digest,
    waive_m2_rv2_gate_in_plan,
)
from aota_forge.governance.project_store import PLAN_LIFECYCLE_ACTIVE, ProjectPlanRecord
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding

spec = importlib.util.spec_from_file_location("gov2_fixture", Path(__file__).with_name("test_gov2_plan_bootstrap_repair.py"))
assert spec and spec.loader
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def test_historical_plan_reconstructs_from_real_journal_and_store(tmp_path: Path) -> None:
    before = fixture.PLAN_BODY.replace("PLAN_REVISION=R1", "PLAN_REVISION=R8")
    amendment = {"kind": "add_work_item", "milestone_id": "M2", "work_item": {
        "id": "W6", "title": "Bootstrap repair validation",
        "objective": "Prove the structured amendment and authority readback.",
        "scope": ["Use the existing Plan lifecycle pipeline."], "dependencies": [],
        "acceptance": ["The resulting Plan preserves W1-W5 and adds this exact Work Item."],
    }}
    before_doc = normalize_portable_plan(before, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    rendered_after, normalized = add_work_item_to_plan(before, before_doc, amendment)
    assert "PLAN_REVISION=R9" in rendered_after
    # The live #005 authority records Store revision 10 while plan.md is at
    # semantic body revision R9. The predecessor body is R8.
    after = rendered_after
    before_digest, after_digest = (hashlib.sha256(x.encode()).hexdigest() for x in (before, after))
    base = tmp_path / "plans"
    plan_dir = base / fixture.PROJECT_ID / fixture.PLAN_ID
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(after, encoding="utf-8")
    root = LocalGovernanceRootBinding.from_trusted_base(base, project_id=fixture.PROJECT_ID)
    target = object_ref_subject(make_id(IdKind.SUBJECT, fixture.PLAN_ID, sub_kind=SubjectKind.PLAN))
    destination = LocalPlanAuthorityDestination(root, fixture.PLAN_ID, target)
    binding = PlanAuthorityBinding(
        plan_id=fixture.PLAN_ID, source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(fixture.PROJECT_ID, fixture.PLAN_ID),
        source_revision=after_digest, source_digest=after_digest,
    )
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / "journal.json")
    try:
        governance.put_plan(ProjectPlanRecord(fixture.PROJECT_ID, fixture.PLAN_ID, PLAN_LIFECYCLE_ACTIVE, binding, 1))
        for expected_revision in range(1, 10):
            governance.compare_and_swap_plan(
                fixture.PROJECT_ID,
                fixture.PLAN_ID,
                expected_revision,
                authority=binding,
            )
        assert governance.get_plan(fixture.PROJECT_ID, fixture.PLAN_ID).revision == 10
        evidence = {"project_id": fixture.PROJECT_ID, "plan_id": fixture.PLAN_ID,
            "authority_source_revision_before": before_digest, "authority_source_revision_after": after_digest,
            "authority_source_digest_before": before_digest, "authority_source_digest_after": after_digest,
            "plan_revision_before": 9, "plan_revision_after": 10, "amendment": normalized,
            "amendment_digest": canonical_plan_amendment_digest(normalized)}
        journal.create_prepared(JournalRecord(
            journal_id="lineage-journal-1", correlation_id="lineage-correlation", attempt_id="lineage-attempt",
            operation="plan_init", typed_target=target, principal="trusted-test", contract_hash="a" * 64,
            idempotency_key="lineage-idempotency", intent_fingerprint="b" * 64,
            subject_expected_revision=9, authority_source_revision=before_digest,
            authority_observed_raw_digest=before_digest, candidate_raw_digest=after_digest,
            normalized_plan_digest="c" * 64, authorization_reference="lineage-auth",
            lease_reference="lineage-lease", authorization_basis="trusted_test",
            journal_state=JournalState.PREPARED, original_raw_digest=before_digest, evidence=evidence,
        ))
        _, applying = journal.cas_transition(
            "lineage-journal-1", 1, JournalState.PREPARED, JournalState.APPLYING
        )
        _, reconciling = journal.cas_transition(
            "lineage-journal-1", applying.journal_revision, JournalState.APPLYING, JournalState.RECONCILING
        )
        journal.cas_transition(
            "lineage-journal-1", reconciling.journal_revision, JournalState.RECONCILING,
            JournalState.VERIFIED_RECOVERED, observed_raw_digest=after_digest, observed_revision=after_digest,
        )
        adapter = object.__new__(closure.ThinMilestoneClosureEvidenceAdapter)
        adapter.project_id = fixture.PROJECT_ID
        adapter.plan_binding = binding
        adapter.governance_root = root
        adapter.journal_store = journal
        adapter.governance_store = governance
        adapter.historical_plan_reader = None
        adapter.historical_plan_lineage_reader = None
        adapter.plan_lineage_reader = None
        exact_entry = journal.get("lineage-journal-1")
        assert exact_entry is not None
        unrelated = SimpleNamespace(
            evidence={"project_id": "foreign_project", "plan_id": "plan_foreign"},
            typed_target=target,
            authority_source_revision="c" * 64,
            observed_revision="d" * 64,
            journal_state=JournalState.VERIFIED,
        )
        entries = [SimpleNamespace(record=unrelated) for _ in range(128)] + [exact_entry]
        adapter.journal_store = SimpleNamespace(list_all=lambda: entries)
        receipt = SimpleNamespace(plan_source_revision=before_digest)
        assert len(adapter._journal_lineage_records()) == 129
        exact_relation = adapter._journal_plan_lineage(receipt)
        assert exact_relation is not None
        assert exact_relation["journal_record"].journal_id == "lineage-journal-1"
        assert exact_relation["project_id"] == fixture.PROJECT_ID
        assert exact_relation["plan_id"] == fixture.PLAN_ID
        document, metadata = adapter._read_historical_plan(before_digest, receipt)
        current_document = normalize_portable_plan(
            after, source_revision=after_digest, source_kind=PORTABLE_PLAN_SOURCE_LOCAL
        )
        adapter._verify_historical_plan_lineage(
            receipt,
            current_plan=current_document,
            required_work_items=closure.M2_HISTORICAL_WORK_ITEMS,
        )
        assert document.source_revision == before_digest
        assert tuple(document.milestone_work_items["M2"]) == ("W1", "W2", "W3", "W4", "W5")
        assert metadata["project_id"] == fixture.PROJECT_ID
        assert "PLAN_REVISION=R9" in after
        assert "PLAN_REVISION=R8" in before

        valid_record = exact_entry.record
        foreign_evidence = dict(valid_record.evidence)
        foreign_evidence["project_id"] = "foreign_project"
        adapter.journal_store = SimpleNamespace(
            list_all=lambda: [replace(valid_record, evidence=foreign_evidence)]
        )
        with pytest.raises(closure.MilestoneClosureEvidenceError) as foreign_error:
            adapter._journal_plan_lineage(receipt)
        assert foreign_error.value.code == "PLAN_LINEAGE_IDENTITY_MISMATCH"

        tampered_evidence = dict(valid_record.evidence)
        tampered_evidence["amendment_digest"] = "0" * 64
        adapter.journal_store = SimpleNamespace(
            list_all=lambda: [replace(valid_record, evidence=tampered_evidence)]
        )
        with pytest.raises(closure.MilestoneClosureEvidenceError) as tampered_error:
            adapter._verify_historical_plan_lineage(
                receipt,
                current_plan=current_document,
                required_work_items=closure.M2_HISTORICAL_WORK_ITEMS,
            )
        assert tampered_error.value.code == "PLAN_LINEAGE_AMENDMENT_DIGEST_MISMATCH"

        adapter.journal_store = SimpleNamespace(
            list_all=lambda: [valid_record, valid_record]
        )
        with pytest.raises(closure.MilestoneClosureEvidenceError) as ambiguous_error:
            adapter._journal_plan_lineage(receipt)
        assert ambiguous_error.value.code == "PLAN_LINEAGE_AMBIGUOUS"
    finally:
        journal.close()
        governance.close()


def test_w1_w5_history_accepts_exact_w6_then_user_waiver_lineage(tmp_path: Path) -> None:
    before = fixture.PLAN_BODY.replace("PLAN_REVISION=R1", "PLAN_REVISION=R8")
    r8_doc = normalize_portable_plan(before, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    w6_amendment = {
        "kind": "add_work_item",
        "milestone_id": "M2",
        "work_item": {
            "id": "W6",
            "title": "Historical closure repair",
            "objective": "Reconstruct bounded predecessor acceptance evidence.",
            "scope": ["M2 closure evidence"],
            "dependencies": ["W5"],
            "acceptance": ["Same-Plan predecessor evidence remains valid."],
        },
    }
    r9, normalized_w6 = add_work_item_to_plan(before, r8_doc, w6_amendment)
    r9 = r9.replace(
        "## Decision D1 - Keep Plan authority canonical",
        "Lifecycle is W6 → integration → RV2 → Doctor → M2 closure; "
        "RV2 is a separate Milestone-level gate, not a Work Item.\n"
        "AC12: No second Plan authority; M2_RV2_REQUIRED=yes and RV2 remains a separate Milestone-level gate.\n\n"
        "## Decision D1 - Keep Plan authority canonical",
    )
    r9_doc = normalize_portable_plan(r9, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    r10, normalized_waiver = waive_m2_rv2_gate_in_plan(
        r9,
        r9_doc,
        {
            "kind": "waive_m2_rv2_gate",
            "milestone_id": "M2",
            "decision_ref": M2_RV2_WAIVER_DECISION_REF,
        },
    )
    r10_digest = hashlib.sha256(r10.encode("utf-8")).hexdigest()
    r10_doc = normalize_portable_plan(
        r10,
        source_revision=r10_digest,
        source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
    )
    assert "PLAN_REVISION=R10" in r10
    assert r10_doc.current_fields["M2_RV2_REQUIRED"] == "no"
    assert r10_doc.current_fields["M2_RV2_WAIVED_BY_USER"] == "yes"

    revisions = [
        hashlib.sha256(body.encode("utf-8")).hexdigest()
        for body in (before, r9, r10)
    ]
    r8_digest, r9_digest, _r10_digest = revisions
    assert _r10_digest == r10_digest
    base = tmp_path / "plans"
    plan_dir = base / fixture.PROJECT_ID / fixture.PLAN_ID
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.md").write_text(r10, encoding="utf-8")
    root = LocalGovernanceRootBinding.from_trusted_base(base, project_id=fixture.PROJECT_ID)
    target = object_ref_subject(make_id(IdKind.SUBJECT, fixture.PLAN_ID, sub_kind=SubjectKind.PLAN))
    binding = PlanAuthorityBinding(
        plan_id=fixture.PLAN_ID,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=local_plan_authority_reference(fixture.PROJECT_ID, fixture.PLAN_ID),
        source_revision=r10_digest,
        source_digest=r10_digest,
    )
    governance = SQLiteProjectGovernanceStore(tmp_path / "governance.sqlite3")
    journal = FileBackedDurableJournalStore(tmp_path / "journal.json")

    def record_transition(
        *,
        journal_id: str,
        before_body: str,
        before_digest: str,
        after_digest: str,
        plan_revision_before: int,
        amendment: dict,
        last_observed_raw_authority_body: str | None = None,
    ) -> None:
        decision_ref = f"decision-{journal_id}"
        amendment_digest = canonical_plan_amendment_digest(amendment)
        evidence = {
            "project_id": fixture.PROJECT_ID,
            "plan_id": fixture.PLAN_ID,
            "decision_ref": decision_ref,
            "authority_source_revision_before": before_digest,
            "authority_source_revision_after": after_digest,
            "authority_source_digest_before": before_digest,
            "authority_source_digest_after": after_digest,
            "plan_revision_before": plan_revision_before,
            "plan_revision_after": plan_revision_before + 1,
            "binding_revision": plan_revision_before + 1,
            "amendment": amendment,
            "amendment_digest": canonical_plan_amendment_digest(amendment),
            "last_observed_raw_authority_body": (
                before_body
                if last_observed_raw_authority_body is None
                else last_observed_raw_authority_body
            ),
            "plan_decision_receipt": {
                "decision_kind": "plan_amendment",
                "decision_ref": decision_ref,
                "project_id": fixture.PROJECT_ID,
                "plan_id": fixture.PLAN_ID,
                "amendment": amendment,
                "amendment_digest": amendment_digest,
                "base_plan_revision": plan_revision_before,
            },
        }
        journal.create_prepared(
            JournalRecord(
                journal_id=journal_id,
                correlation_id=f"corr-{journal_id}",
                attempt_id=f"attempt-{journal_id}",
                operation="plan_init",
                typed_target=target,
                principal="task-main",
                contract_hash="a" * 64,
                idempotency_key=f"idem-{journal_id}",
                intent_fingerprint="b" * 64,
                subject_expected_revision=plan_revision_before,
                authority_source_revision=before_digest,
                authority_observed_raw_digest=before_digest,
                candidate_raw_digest=after_digest,
                normalized_plan_digest="c" * 64,
                authorization_reference=decision_ref,
                lease_reference=f"lease-{journal_id}",
                authorization_basis="trusted_test",
                journal_state=JournalState.PREPARED,
                original_raw_digest=before_digest,
                evidence=evidence,
            )
        )
        _, applying = journal.cas_transition(journal_id, 1, JournalState.PREPARED, JournalState.APPLYING)
        _, reconciling = journal.cas_transition(
            journal_id,
            applying.journal_revision,
            JournalState.APPLYING,
            JournalState.RECONCILING,
        )
        journal.cas_transition(
            journal_id,
            reconciling.journal_revision,
            JournalState.RECONCILING,
            JournalState.VERIFIED_RECOVERED,
            observed_raw_digest=after_digest,
            observed_revision=after_digest,
        )

    try:
        governance.put_plan(ProjectPlanRecord(fixture.PROJECT_ID, fixture.PLAN_ID, PLAN_LIFECYCLE_ACTIVE, binding, 1))
        for expected_revision in range(1, 11):
            governance.compare_and_swap_plan(
                fixture.PROJECT_ID,
                fixture.PLAN_ID,
                expected_revision,
                authority=binding,
            )
        assert governance.get_plan(fixture.PROJECT_ID, fixture.PLAN_ID).revision == 11
        record_transition(
            journal_id="lineage-w6",
            before_body=before,
            before_digest=r8_digest,
            after_digest=r9_digest,
            plan_revision_before=9,
            amendment=normalized_w6,
        )
        record_transition(
            journal_id="lineage-rv2-waiver",
            before_body=r9,
            before_digest=r9_digest,
            after_digest=r10_digest,
            plan_revision_before=10,
            amendment=normalized_waiver,
            last_observed_raw_authority_body=r10[:1024],
        )

        adapter = object.__new__(closure.ThinMilestoneClosureEvidenceAdapter)
        adapter.project_id = fixture.PROJECT_ID
        adapter.plan_binding = binding
        adapter.governance_root = root
        adapter.journal_store = journal
        adapter.governance_store = governance
        adapter.historical_plan_reader = None
        adapter.historical_plan_lineage_reader = None
        adapter.plan_lineage_reader = None
        receipt = SimpleNamespace(plan_source_revision=r8_digest)
        relation = adapter._journal_plan_lineage(receipt, r10_doc)
        assert relation is not None
        assert relation["lineage_kind"] == "m2_w6_then_user_waiver"
        waiver_relation = relation["user_waiver_relation"]
        waiver_summary = waiver_relation["last_observed_raw_authority_body"]
        assert isinstance(waiver_summary, str)
        assert len(waiver_summary) < len(r9)
        assert hashlib.sha256(waiver_summary.encode("utf-8")).hexdigest() != r9_digest
        reconstructed_r9 = adapter._verified_m2_rv2_waiver_predecessor(r10, waiver_relation, r10_doc)
        assert reconstructed_r9 == r9
        assert hashlib.sha256(reconstructed_r9.encode("utf-8")).hexdigest() == r9_digest
        replayed_r10, _ = waive_m2_rv2_gate_in_plan(
            reconstructed_r9,
            normalize_portable_plan(
                reconstructed_r9,
                source_revision=r9_digest,
                source_kind=PORTABLE_PLAN_SOURCE_LOCAL,
            ),
            normalized_waiver,
        )
        assert replayed_r10 == r10
        assert hashlib.sha256(replayed_r10.encode("utf-8")).hexdigest() == r10_digest
        tampered_relations = []
        changed = deepcopy(waiver_relation)
        changed["amendment"]["kind"] = "add_work_item"
        tampered_relations.append(changed)
        changed = deepcopy(waiver_relation)
        changed["amendment"]["decision_ref"] = "user-decision:foreign"
        tampered_relations.append(changed)
        changed = deepcopy(waiver_relation)
        changed["amendment_digest"] = "0" * 64
        tampered_relations.append(changed)
        changed = deepcopy(waiver_relation)
        changed["project_id"] = "foreign_project"
        tampered_relations.append(changed)
        changed = deepcopy(waiver_relation)
        changed["authority_source_digest_before"] = "0" * 64
        tampered_relations.append(changed)
        changed = deepcopy(waiver_relation)
        changed["authority_source_revision_before"] = "0" * 64
        tampered_relations.append(changed)
        for tampered in tampered_relations:
            with pytest.raises(closure.MilestoneClosureEvidenceError):
                adapter._verified_m2_rv2_waiver_predecessor(r10, tampered, r10_doc)
        with pytest.raises(closure.MilestoneClosureEvidenceError):
            adapter._verified_m2_rv2_waiver_predecessor(
                r10.replace(f"PLAN_ID={fixture.PLAN_ID}", "PLAN_ID=foreign_plan", 1),
                waiver_relation,
                r10_doc,
            )
        historical, _metadata = adapter._read_historical_plan(r8_digest, receipt, lineage=relation)
        adapter._verify_historical_plan_lineage(
            receipt,
            current_plan=r10_doc,
            required_work_items=closure.M2_HISTORICAL_WORK_ITEMS,
        )
        assert str(historical.source_revision) == r8_digest
        assert tuple(historical.milestone_work_items["M2"]) == ("W1", "W2", "W3", "W4", "W5")
    finally:
        journal.close()
        governance.close()
