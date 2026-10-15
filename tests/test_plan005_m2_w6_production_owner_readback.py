from pathlib import Path
import hashlib
import pytest
from aota_forge.core.execution.durable_state import FileBackedExecutionStateStore
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.adapters.opencode.host_client import OpenCodeHostClient, message_finish, message_info, message_role, message_text_parts
from aota_forge.runtime.config import EXECUTOR_OPENCODE, load_runtime_config
from aota_forge.governance import milestone_closure_evidence as c
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane.authorized_roots import LocalGovernanceRootBinding
from aota_forge.work_plane.steward_finalizer import SubprocessGitPort
P="aota_forge"; N="plan_005_governance_self_hosting_routing_runtime_lifecycle"; W=("W1","W2","W3","W4","W5")
def test_real_owner_readbacks():
 root=Path(__file__).parents[1]; s=root/".aota"
 g=SQLiteProjectGovernanceStore(s/"governance.sqlite3"); j=FileBackedDurableJournalStore(s/"governance-journal.json")
 origin="ses_edc63efd5ffeuHX8gWSHH8UFkk"
 x=FileBackedExecutionStateStore(s/"execution-sessions"/f"{hashlib.sha256(origin.encode()).hexdigest()[:32]}.json"); r=c.FileBackedParentWorkAcceptanceProjectionStore(root)
 try:
  plan=g.get_plan(P,N)
  if plan is None: pytest.fail("ProjectGovernanceStore.get_plan returned no exact #005 Plan")
  def ok(e):
   v=e.record.evidence; a=v.get("amendment") if isinstance(v,dict) else None; i=a.get("work_item") if isinstance(a,dict) else None
   return v.get("project_id")==P and v.get("plan_id")==N and a and a.get("kind")=="add_work_item" and a.get("milestone_id")=="M2" and isinstance(i,dict) and i.get("id")=="W6"
  hits=[e for e in j.list_all() if ok(e)]
  if len(hits)!=1: pytest.fail(f"real W6 JournalRecord count={len(hits)}")
  rec=hits[0].record; ev=dict(rec.evidence); a=ev["amendment"]; d=ev.get("plan_decision_receipt") or ev.get("semantic_decision_receipt")
  if not isinstance(d,dict): pytest.fail(f"real W6 decision missing; keys={sorted(ev)}")
  assert ev.get("amendment_digest")==c.canonical_plan_amendment_digest(a)
  rs={z.work_item_id:z for z in r.list_for_milestone(P,N,"M2")}; miss=[w for w in W if w not in rs]
  if miss: pytest.fail(f"real immutable W1-W5 receipts missing: {miss}")
  missing=[w for w in W if x.get(rs[w].canonical_task_id) is None]
  assert missing==[]
  cards={}
  for w in W:
   z=rs[w]; record=x.get(z.canonical_task_id); card=c.WorkerResultCard.from_dict(record.worker_result_card)
   assert card.task_ref==z.canonical_task_id and card.outcome.value=="success"
   assert record.worker_result_card_digest==card.card_digest==z.completion_card_digest
   assert card.result_handoff_ref.digest==z.result_digest
   cards[w]=(z.receipt_digest,z.result_digest,z.completion_card_digest,card.result_handoff_ref.digest)
  journal_entries=j.list_all()
  assert 129 <= len(journal_entries) <= c.HISTORICAL_PLAN_LINEAGE_MAX_JOURNAL_RECORDS
  lineage_probe=object.__new__(c.ThinMilestoneClosureEvidenceAdapter)
  lineage_probe.project_id=P; lineage_probe.plan_binding=plan.authority; lineage_probe.governance_store=g; lineage_probe.journal_store=j
  exact_lineage=lineage_probe._journal_plan_lineage(rs["W1"])
  assert exact_lineage is not None and exact_lineage.get("project_id")==P and exact_lineage.get("plan_id")==N
  assert exact_lineage.get("journal_record").journal_id==rec.journal_id
  lineage_probe.governance_root=LocalGovernanceRootBinding.from_trusted_base(root.parent/"plans",project_id=P)
  lineage_probe.historical_plan_reader=None; lineage_probe.plan_lineage_reader=None
  current_plan,_=lineage_probe._current_plan()
  reconstructed=[]
  for w in W:
   lineage_probe._verify_historical_plan_lineage(rs[w],current_plan=current_plan,required_work_items=c.M2_HISTORICAL_WORK_ITEMS)
   reconstructed.append(w)
  assert tuple(reconstructed)==W
  cfg=load_runtime_config()
  if cfg.executor!=EXECUTOR_OPENCODE:
   assert cfg.host_endpoint is None
   return
  assert cfg.host_endpoint
  host=OpenCodeHostClient(cfg.host_endpoint); raw_messages=host.fetch_session_messages(origin,directory=str(root))
  parent_messages=tuple({"session_ref":origin,"message_id":str(message_info(m).get("id") or ""),"role":str(message_role(m) or ""),"finish":str(message_finish(m) or ""),"text":"".join(message_text_parts(m))} for m in raw_messages)
  q=c.ThinMilestoneClosureEvidenceAdapter(project_id=P,project_root=root,governance_root=s,plan_binding=plan.authority,parent_session_ref=origin,execution_store=x,sandbox=None,governance_store=g,receipt_store=r,parent_decision_reader=lambda _session: parent_messages,journal_store=j)
  for w in W:
   z=rs[w]; q._verify_parent_decision(decision_ref=z.decision_ref,decision_digest=z.decision_digest,messages=parent_messages)
  body=ev.get("last_observed_raw_authority_body"); rev=next((z for z in body.splitlines() if z.startswith("PLAN_REVISION=")),None) if isinstance(body,str) else None
  assert rev=="PLAN_REVISION=R9" and isinstance(body,str) and len(body)<=1000 and rec.observed_raw_digest==plan.authority.source_revision
  i=a["work_item"]
  assert i["title"]=="Bounded Milestone Closure Historical Evidence Reconstruction Repair" and i["dependencies"]==["W1","W2","W3","W4","W5"]
  assert hashlib.sha256(body.encode()).hexdigest()!=rec.observed_raw_digest
  assert ev.get("reconciliation_classification")=="CANDIDATE_OBSERVED" and ev.get("amendment_phase")=="verified" and ev.get("classification")=="CANDIDATE_OBSERVED" and ev.get("authority_switch")=="unchanged" and ev.get("cutover_phase")=="prepared" and ev.get("binding_revision")==10
  if "W2" in W:
   z=rs["W2"]; p=z.production_provenance or {}; subject=p.get("runtime_subject") or {}
   assert z.production_evidence_ref and z.production_evidence_digest and z.production_provenance_digest
   assert p.get("eligibility",{}).get("provenance_valid") is True and p.get("eligibility",{}).get("eligible") is True
   assert subject.get("project_id")==P and SubprocessGitPort().is_ancestor(root,subject["source_commit"],z.accepted_frontier)
   assert SubprocessGitPort().resolve_tree(root,subject["source_commit"])==subject["source_tree"]
  if "W5" in W:
   git=SubprocessGitPort(); head=git.resolve_ref(root,"HEAD"); z=rs["W5"]
   assert head and git.is_ancestor(root,z.accepted_frontier,head)
  q=object.__new__(c.ThinMilestoneClosureEvidenceAdapter); q.project_id=P; q.plan_binding=plan.authority; q.governance_store=g; q.journal_store=j
  rel=q._journal_lineage_relation(rec)
  assert rel.get("project_id")==P and rel.get("plan_id")==N and rel.get("milestone_id")=="M2" and rel.get("work_item_id")=="W6"
  assert rec.subject_expected_revision==9 and rel.get("plan_revision_after")==plan.revision
 finally:
  x.close(); j.close(); g.close()
