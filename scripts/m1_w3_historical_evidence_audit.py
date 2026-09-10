"""M1/W3 Part B — historical #40/M1 evidence integrity audit utility (AF #45).

Strictly READ-ONLY over historical evidence. Never mutates historical
stores, repositories, or Hermes state. Writes only its own bounded JSON
lineage report to a caller-supplied output directory.

Audits the historical claim around HISTORICAL_M1_SESSION=20260909_135410_662ec5
(claimed genuine autonomous clean replay) by tracing provenance of every
decisive M1 acceptance artifact:

  * execution/coordinator stores (dispatch timestamps, attempt ids, adapter
    handles, origin_session_ref)
  * Hermes run dirs (mechanical receipts vs execution records)
  * Hermes session DBs (task-main + worker activity actually observed)
  * repository commits/trees (clean-replay branch, product candidate)
  * incident branch df58517 + I40-B002 history + #40 acceptance material

Verdict levels (exactly one):
  VALID | VALID_WITH_PROVENANCE_FINDING |
  INVALID_REQUIRES_GOVERNANCE_RECONCILIATION
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

HISTORICAL_M1_SESSION = "20260909_135410_662ec5"
MORNING_SESSION = "20260909_102517_5fff0e"
FAILED_SESSION = "20260908_234034_e47d30"
REPLAY_BRANCH = "aota/dogfood-001/m1-autonomous-replay"
REPLAY_COMMIT = "fd2bae6f7c0b9bd2459108c02eb506a85cb2b049"
HISTORICAL_PRODUCT_COMMIT = "f247e275af78bd262878ea9d6c2328e59aa0e3f6"
EXPECTED_TREE = "d4ccb8da000823a87d68b76277c4fb638e6e3857"

DOGFOOD_MAIN_AOTA = Path("/home/latios/workspace/aota-forge-dogfood/.aota")
REPLAY_AOTA = Path("/home/latios/workspace/.aota-worktrees/aota-forge-dogfood/M1/autonomous-replay/.aota")
DOGFOOD_REPO = Path("/home/latios/workspace/aota-forge-dogfood")
RUNS_ROOT = Path("/home/latios/.aota-forge/hermes-runtime/runs")
TM_DB = Path("/home/latios/.hermes/profiles/aota-task-main/state.db")
W_DB = Path("/home/latios/.hermes/profiles/aota-worker/state.db")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True, timeout=30)
    return out.stdout.strip()


def main() -> int:
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/w3-part-b-audit")
    outdir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {"HISTORICAL_M1_SESSION": HISTORICAL_M1_SESSION, "artifacts": []}
    artifacts = report["artifacts"]
    assert isinstance(artifacts, list)

    def artifact(**kw: object) -> None:
        artifacts.append(kw)

    # 1. Execution-store lineage: replay vs morning original.
    orig_ex = load_json(DOGFOOD_MAIN_AOTA / "execution.json")
    replay_ex = load_json(REPLAY_AOTA / "execution.json")
    artifact(type="execution_store_original", path=str(DOGFOOD_MAIN_AOTA / "execution.json"),
             tasks=sorted(orig_ex), origins=sorted({r.get("origin_session_ref") for r in orig_ex.values()}),
             dispatched=sorted({r.get("dispatched_at") for r in orig_ex.values()}),
             handles=sorted({r.get("adapter_handle") for r in orig_ex.values()}),
             cards=sorted({r.get("worker_result_card_digest") for r in orig_ex.values()}))
    artifact(type="execution_store_replay", path=str(REPLAY_AOTA / "execution.json"),
             tasks=sorted(replay_ex), origins=sorted({r.get("origin_session_ref") for r in replay_ex.values()}),
             dispatched=sorted({r.get("dispatched_at") for r in replay_ex.values()}),
             handles=sorted({r.get("adapter_handle") for r in replay_ex.values()}),
             cards=sorted({r.get("worker_result_card_digest") for r in replay_ex.values()}))
    exec_only_origin_diff = (
        sorted(orig_ex) == sorted(replay_ex)
        and all({k for k in orig_ex[t] if orig_ex[t].get(k) != replay_ex[t].get(k)} == {"origin_session_ref"} for t in orig_ex)
    )
    same_handles = {r.get("adapter_handle") for r in orig_ex.values()} == {r.get("adapter_handle") for r in replay_ex.values()}
    predates = all(str(r.get("dispatched_at", "")) < "2026-09-09T05:00:00Z" for r in replay_ex.values())
    rebound = all(r.get("origin_session_ref") == HISTORICAL_M1_SESSION for r in replay_ex.values())
    report["EXECUTION_STORE_ONLY_ORIGIN_DIFF"] = exec_only_origin_diff
    report["ADAPTER_HANDLES_IDENTICAL"] = same_handles
    report["REPLAY_DISPATCHES_PREDATE_SESSION"] = predates
    report["ORIGIN_SESSION_REWRITE_FOUND"] = rebound

    # 2. Coordinator lineage.
    orig_coord = load_json(DOGFOOD_MAIN_AOTA / "coordinator.json")
    replay_coord = load_json(REPLAY_AOTA / "coordinator.json")
    for label, coord in (("original", orig_coord), ("replay", replay_coord)):
        st = coord.get("aota_forge_dogfood:M1", {})
        artifact(type=f"coordinator_store_{label}", origin=st.get("origin_task_main_session_ref"),
                 revision=st.get("coordinator_revision"), status=st.get("status"),
                 wi_status=st.get("wi_status"), entry_base=st.get("entry_base"),
                 binding_digests={w: (b.get("completion_card_digest") or "")[:16] for w, b in (st.get("bindings") or {}).items()})
    report["COORDINATOR_REVISION_IDENTICAL"] = (
        orig_coord["aota_forge_dogfood:M1"].get("coordinator_revision") == replay_coord["aota_forge_dogfood:M1"].get("coordinator_revision"))
    report["COORDINATOR_NEVER_RECONCILED"] = all(
        s == "COMPLETION_PENDING_RECONCILIATION"
        for s in replay_coord["aota_forge_dogfood:M1"].get("wi_status", {}).values())

    # 3. Mechanical receipts vs execution records (preserved handles).
    receipts: dict[str, object] = {}
    for tid, rec in replay_ex.items():
        rid = str(rec.get("adapter_handle", "")).removeprefix("hermes-host-")
        rfile, ufile = RUNS_ROOT / rid / "receipt.json", RUNS_ROOT / rid / "usage.json"
        rj = json.loads(rfile.read_text()) if rfile.is_file() else None
        uj = json.loads(ufile.read_text()) if ufile.is_file() else None
        receipts[tid] = {"receipt_status": (rj or {}).get("status"), "exit": (rj or {}).get("exit_code"),
                         "usage_session": (uj or {}).get("session_id"),
                         "record_state": rec.get("canonical_task_state"),
                         "card_digest": str(rec.get("worker_result_card_digest"))[:16]}
    report["MECHANICAL_RECEIPTS"] = receipts
    report["PRESERVED_TIMEOUT_WORKERS_WITH_CARDS"] = sorted(
        tid for tid, r in receipts.items()
        if isinstance(r, dict) and r.get("receipt_status") == "timeout" and r.get("usage_session") is None and r.get("card_digest"))

    # 4. Hermes session activity (read-only).
    def session_stats(db: Path, sid: str) -> dict[str, object]:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT started_at, ended_at, message_count, tool_call_count, api_call_count, end_reason FROM sessions WHERE id=?", (sid,)).fetchone()
            return dict(zip(("started_at", "ended_at", "messages", "tools", "api", "end"), row)) if row else {"found": False}
        finally:
            con.close()
    for sid in (HISTORICAL_M1_SESSION, MORNING_SESSION, FAILED_SESSION):
        artifact(type="task_main_session", session=sid, stats=session_stats(TM_DB, sid))
    replay_workers = ["20260909_135152_0987e5", "20260909_135314_aa2a67", "20260909_135433_cf2289"]
    for sid in replay_workers:
        artifact(type="replay_worker_session", session=sid, stats=session_stats(W_DB, sid))
    replay_tm = session_stats(TM_DB, HISTORICAL_M1_SESSION)
    report["REPLAY_SESSION_DURATION_S"] = (replay_tm.get("ended_at") or 0) - (replay_tm.get("started_at") or 0)
    report["REPLAY_SESSION_TOOL_COUNT"] = replay_tm.get("tools")
    report["REPLAY_WORKERS_ZERO_TOOL_CALLS"] = all(
        (session_stats(W_DB, s).get("tools") or 0) == 0 for s in replay_workers)

    # 5. Repository provenance.
    artifact(type="replay_commit", commit=REPLAY_COMMIT,
             author_date=git(DOGFOOD_REPO, "log", "-1", "--format=%ad", "--date=iso", REPLAY_COMMIT),
             parent=git(DOGFOOD_REPO, "rev-list", "--parents", "-1", REPLAY_COMMIT).split()[1:],
             tree=git(DOGFOOD_REPO, "rev-parse", f"{REPLAY_COMMIT}^{{tree}}"))
    artifact(type="historical_product_commit", commit=HISTORICAL_PRODUCT_COMMIT,
             tree=git(DOGFOOD_REPO, "rev-parse", f"{HISTORICAL_PRODUCT_COMMIT}^{{tree}}"))
    report["TREES_EQUAL"] = git(DOGFOOD_REPO, "rev-parse", f"{REPLAY_COMMIT}^{{tree}}") == EXPECTED_TREE == git(DOGFOOD_REPO, "rev-parse", f"{HISTORICAL_PRODUCT_COMMIT}^{{tree}}")

    # 6. Genuine replay-window dispatches (physical runs for dogfood tasks 05:45-06:00Z).
    genuine = []
    for d in sorted(RUNS_ROOT.iterdir()):
        mf = d / "marker.json"
        if not mf.is_file():
            continue
        try:
            m = json.loads(mf.read_text())
        except Exception:
            continue
        if "dogfood" not in str(m.get("canonical_task_id") or ""):
            continue
        ts = float(m.get("dispatched_at_wall") or 0)
        if 1788932700 <= ts <= 1788933600:  # 05:45-06:00 UTC 9/9
            genuine.append({"task": m.get("canonical_task_id"), "handle": m.get("adapter_handle"), "wall": ts})
    report["GENUINE_REPLAY_WINDOW_DISPATCHES"] = genuine
    report["GENUINE_REPLAY_BEYOND_W1"] = sorted({g["task"] for g in genuine if ":W1:" not in str(g["task"])})
    report["HISTORICAL_EVIDENCE_ARTIFACT_COUNT"] = len(artifacts)
    report["HISTORICAL_EVIDENCE_LINEAGE_COMPLETE"] = True

    # Verdict.
    operational_relied_on_rebound = bool(same_handles and predates and rebound and exec_only_origin_diff)
    independent_operational_support = bool(report["GENUINE_REPLAY_BEYOND_W1"]) or not bool(report["COORDINATOR_NEVER_RECONCILED"])
    if not operational_relied_on_rebound:
        verdict = "VALID"
    elif independent_operational_support:
        verdict = "VALID_WITH_PROVENANCE_FINDING"
    else:
        verdict = "INVALID_REQUIRES_GOVERNANCE_RECONCILIATION"
    report["M1_HISTORICAL_EVIDENCE_VERDICT"] = verdict
    report["M1_ACCEPTANCE_AUTOMATICALLY_REVOKED"] = "no"
    report["GOVERNANCE_RECONCILIATION_REQUIRED"] = "yes" if verdict.startswith("INVALID") else "no"

    (outdir / "w3_part_b_evidence.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "artifacts"}, indent=2, default=str))
    print(f"artifacts={len(artifacts)} verdict={verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
