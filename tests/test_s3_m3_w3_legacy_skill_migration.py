"""S3 M3 W3 Legacy Migration / RETAIN-REWRITE-REPLACE-RETIRE Proof.

Validates W3 classification artifact against W2 inventory snapshot.
Proves deterministic, evidence-backed final disposition for every legacy Skill.
"""

from __future__ import annotations

import json
import pathlib
import re
import hashlib
import subprocess
import pytest

FIXTURE_W2 = pathlib.Path(__file__).parent / "fixtures" / "s3_m3_legacy_skill_inventory.json"
FIXTURE_W3 = pathlib.Path(__file__).parent / "fixtures" / "s3_m3_legacy_skill_migration_classification.json"

ALLOWED_FINAL = {"RETAIN", "REWRITE", "REPLACE_BY_FORGE", "RETIRE"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture
def w2_data():
    assert FIXTURE_W2.exists(), f"W2 fixture missing: {FIXTURE_W2}"
    return json.loads(FIXTURE_W2.read_text(encoding="utf-8"))


@pytest.fixture
def w3_data():
    assert FIXTURE_W3.exists(), f"W3 fixture missing: {FIXTURE_W3}"
    return json.loads(FIXTURE_W3.read_text(encoding="utf-8"))


@pytest.fixture
def w2_entries(w2_data):
    return w2_data["entries"]


@pytest.fixture
def w3_entries(w3_data):
    return w3_data["entries"]


# A. Same inventory universe
def test_same_inventory_universe(w2_data, w3_data, w2_entries, w3_entries):
    assert w2_data["source_repository"] == "wzjcccc-dotcom/aota-hermes-tools"
    assert w3_data["source_repository"] == "wzjcccc-dotcom/aota-hermes-tools"
    assert len(w2_entries) == 28, f"W2 inventory must be 28, got {len(w2_entries)}"
    assert len(w3_entries) == 28, f"W3 classification must be 28, got {len(w3_entries)}"
    assert w3_data["summary"]["tracked_legacy_skill_count"] == 28
    assert w3_data["summary"]["inventoried_legacy_skill_count"] == 28
    assert w3_data["summary"]["final_classification_count"] == 28
    assert w3_data["source_inventory"] == "tests/fixtures/s3_m3_legacy_skill_inventory.json"
    # No additions/deletions: skill_name sets must match
    w2_names = set(e["skill_name"] for e in w2_entries)
    w3_names = set(e["skill_name"] for e in w3_entries)
    assert w2_names == w3_names, f"name mismatch {w2_names ^ w3_names}"
    w2_paths = set(e["source_path"] for e in w2_entries)
    w3_paths = set(e["source_path"] for e in w3_entries)
    assert w2_paths == w3_paths


# B. Identity preservation
def test_identity_preservation(w2_entries, w3_entries):
    w2_map = {e["skill_name"]: e for e in w2_entries}
    for w3 in w3_entries:
        name = w3["skill_name"]
        w2 = w2_map[name]
        assert w3["source_path"] == w2["source_path"], f"{name} source_path mismatch"
        assert w3["content_sha256"] == w2["content_sha256"], f"{name} digest mismatch"
        assert SHA256_RE.fullmatch(w3["content_sha256"]), f"{name} digest not 64 lower hex"
        assert w3["content_sha256"] == w3["content_sha256"].lower()
        assert w3["content_size"] == w2["content_size"] if "content_size" in w3 else True


# C. Source revision
def test_source_revision_matches_w2(w2_data, w3_data):
    assert "source_revision" in w2_data
    assert "source_revision" in w3_data
    rev_w2 = w2_data["source_revision"]
    rev_w3 = w3_data["source_revision"]
    assert SHA40_RE.fullmatch(rev_w2)
    assert SHA40_RE.fullmatch(rev_w3)
    assert rev_w2 == rev_w3, f"revision mismatch W2 {rev_w2} vs W3 {rev_w3}"
    assert rev_w3 == "d9cf0adc2d448997d291633e474cb7c34e65615e"
    assert w3_data.get("classification_source_revision") == rev_w3
    assert w3_data.get("classification_revision_matches_w2") is True


# D. Final vocabulary
def test_final_vocabulary(w3_entries):
    for e in w3_entries:
        cls = e.get("final_classification")
        assert cls in ALLOWED_FINAL, f"{e['skill_name']} invalid classification {cls!r}"
        assert cls != "UNKNOWN_NEEDS_RECONCILIATION"


# E. UNKNOWN zero
def test_unknown_zero(w3_data, w3_entries):
    unknowns = [e for e in w3_entries if e.get("final_classification") == "UNKNOWN_NEEDS_RECONCILIATION"]
    assert len(unknowns) == 0, f"found {len(unknowns)} unknowns"
    assert w3_data["summary"]["unknown_needs_reconciliation_count_after"] == 0
    # W2 must still have 28 UNKNOWN (evidence layer not mutated)
    w2 = json.loads(FIXTURE_W2.read_text())
    unk_w2 = [x for x in w2["entries"] if x.get("preliminary_classification") == "UNKNOWN_NEEDS_RECONCILIATION"]
    assert len(unk_w2) == 28, "W2 inventory must still have 28 UNKNOWN (immutable evidence layer)"


# F. Concrete replacement evidence
def test_replace_requires_concrete_seam(w3_entries, w3_data):
    assert w3_data["classification_policy"]["replace_by_forge_requires_concrete_accepted_seam"] is True
    for e in w3_entries:
        if e["final_classification"] == "REPLACE_BY_FORGE":
            ev = e.get("forge_replacement_evidence")
            assert isinstance(ev, list) and len(ev) > 0, f"{e['skill_name']} REPLACE must have non-empty seam evidence"
            joined = " ".join(ev)
            # must name concrete path and symbol and lineage
            assert "aota_forge/" in joined, f"{e['skill_name']} seam must name accepted Forge path"
            # must contain accepted lineage marker
            assert "accepted" in joined.lower() or "frontier" in joined.lower(), f"{e['skill_name']} seam must mention accepted lineage"
            for item in ev:
                assert isinstance(item, str) and item.strip()
                # must not be future planned
                lower = item.lower()
                assert "future" not in lower and "planned" not in lower, f"{e['skill_name']} must not use future planned capability"
            # also check provenance not using unaccepted branch
            assert w3_data["classification_policy"]["concurrent_unaccepted_branch_used_as_replacement_proof"] is False


# G. RETIRE evidence not solely unreferenced
def test_retire_not_solely_unreferenced(w3_entries, w3_data):
    assert w3_data["classification_policy"]["unreferenced_alone_implies_retire"] is False
    for e in w3_entries:
        if e["final_classification"] == "RETIRE":
            reasons = e.get("classification_reasons", [])
            assert isinstance(reasons, list) and len(reasons) >= 2
            txt = " ".join(str(x) for x in reasons).lower()
            # must contain obsolete/superseded/conflict etc beyond unreferenced
            assert any(k in txt for k in ["obsolete", "superseded", "removed", "no residual", "alias"]), \
                f"{e['skill_name']} RETIRE must prove obsolete/superseded beyond unreferenced, got {reasons}"
            # unreferenced alone insufficient: reasons cannot be only unreferenced
            assert "unreferenced" not in txt or "superseded" in txt or "obsolete" in txt, \
                f"{e['skill_name']} RETIRE cannot rely solely on unreferenced"


# H. REWRITE evidence identifies retained value + incompatible
def test_rewrite_evidence(w3_entries):
    for e in w3_entries:
        if e["final_classification"] == "REWRITE":
            reasons = e.get("classification_reasons", [])
            txt = " ".join(str(x) for x in reasons).lower()
            # must identify retained procedural value
            assert any(k in txt for k in ["retain", "remains", "useful", "value"]), \
                f"{e['skill_name']} REWRITE must identify retained procedural value"
            # must identify what is obsolete/incompatible
            assert any(k in txt for k in ["obsolete", "incompatible", "malformed", "mix", "assumption"]), \
                f"{e['skill_name']} REWRITE must identify obsolete/incompatible semantics"


# I. Alias absence
def test_no_legacy_profile_alias_mapping(w3_data, w3_entries):
    assert w3_data["classification_policy"]["legacy_profile_alias_mapping_created"] is False
    raw = json.dumps(w3_data).lower()
    forbidden = ["architect -> analyst", "debugger -> analyst", "task-coordinator -> task-main", "friday -> task-main"]
    for alias in forbidden:
        assert alias not in raw, f"forbidden alias mapping {alias} found"
    # also ensure no alias_mapping field invents mapping
    # allowed flags are false, but content must not contain mapping dict
    for e in w3_entries:
        assert "alias_mapping" not in json.dumps(e).lower() or e.get("legacy_profile_alias_mapping_created") is False


# J. Determinism
def test_classification_order_deterministic(w3_entries, w3_data):
    sorted_entries = sorted(w3_entries, key=lambda x: (x["skill_name"], x["source_path"]))
    assert w3_entries == sorted_entries, "entries must be canonical sorted by skill_name, source_path"
    assert w3_data["classification_policy"]["deterministic_order"] == "skill_name, source_path ascending"
    assert w3_data["classification_policy"]["input_order_is_authority"] is False


# Additional: evidence completeness
def test_evidence_completeness(w3_entries):
    for e in w3_entries:
        assert "final_classification" in e
        assert "classification_reasons" in e and isinstance(e["classification_reasons"], list) and len(e["classification_reasons"]) > 0
        assert "usage_evidence" in e and isinstance(e["usage_evidence"], dict)
        assert "ownership_evidence" in e and isinstance(e["ownership_evidence"], dict)
        assert "forge_replacement_evidence" in e and isinstance(e["forge_replacement_evidence"], list)
        assert "evidence_refs" in e and isinstance(e["evidence_refs"], list) and len(e["evidence_refs"]) > 0
        assert "authority_compatibility" in e
        assert "migration_action_summary" in e and isinstance(e["migration_action_summary"], str) and e["migration_action_summary"].strip()
        # usage evidence must have usage_state
        assert e["usage_evidence"].get("usage_state") in {"ACTIVE", "REFERENCE", "PROFILE_SPECIFIC", "UNREFERENCED_BY_CURRENT_ASSEMBLY"}
        # ownership must have purpose
        assert "skill_description_purpose" in e["ownership_evidence"] or "profile_runtime_assembly_usage" in e["ownership_evidence"]


# Filename-only classification must be zero
def test_no_filename_only_classification(w3_data, w3_entries):
    assert w3_data["classification_policy"]["filename_only_classification_count"] == 0
    for e in w3_entries:
        # reasons must mention more than filename
        txt = " ".join(e.get("classification_reasons", []))
        assert len(txt) > 50, f"{e['skill_name']} reasons too short, filename-only suspected"
        # should mention procedural content or usage or forge evidence
        lower = txt.lower()
        assert any(k in lower for k in ["procedure", "content", "usage", "evidence", "forge", "compatible", "obsolete", "superseded"]), \
            f"{e['skill_name']} must be evaluated beyond filename"


# Malformed skills explicitly reconciled
def test_malformed_skills_reconciled(w2_entries, w3_entries, w3_data):
    malformed_w2 = [e for e in w2_entries if e.get("frontmatter_malformed")]
    assert len(malformed_w2) == 2, f"expected 2 malformed, got {len(malformed_w2)}"
    assert w3_data["summary"]["malformed_legacy_skill_count"] == 2
    assert w3_data["summary"]["malformed_skills_explicitly_reconciled"] is True
    w3_map = {e["skill_name"]: e for e in w3_entries}
    for m in malformed_w2:
        name = m["skill_name"]
        w3 = w3_map[name]
        # must not auto RETIRE/REWRITE without semantic evidence; check reasons mention malformed/semantic
        txt = " ".join(w3.get("classification_reasons", [])).lower()
        assert "malformed" in txt or "semantic" in txt or "frontmatter" in txt or "incompatible" in txt, \
            f"malformed {name} must be explicitly reconciled with semantic evidence, got {w3.get('classification_reasons')}"
        # final classification must not be UNKNOWN and must be one of four
        assert w3["final_classification"] in ALLOWED_FINAL


# Usage state not equals migration disposition
def test_usage_state_not_equals_disposition(w3_data, w3_entries):
    assert w3_data["classification_policy"]["usage_state_equals_migration_disposition"] is False


# Legacy profile alias not created, no S1 hot file touched, no runtime authority
def test_governance_flags(w3_data):
    assert w3_data["classification_policy"]["legacy_profile_alias_mapping_created"] is False
    assert w3_data["classification_policy"]["migration_classification_is_runtime_authority"] is False
    assert w3_data["classification_policy"]["migration_classification_auto_applied"] is False
    assert w3_data["classification_policy"]["actual_legacy_migration_performed"] is False
    assert w3_data["classification_policy"]["legacy_source_mutation_performed"] is False
    assert w3_data["classification_policy"]["s1_shared_hot_file_touched"] is False
    assert w3_data["classification_policy"]["classification_count_targets_predetermined"] is False


# Runtime production source not changed (no work_plane mutation)
def test_no_runtime_production_source_changed():
    repo_root = pathlib.Path(__file__).parent.parent
    for p in (repo_root / "aota_forge" / "work_plane").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "s3_m3_legacy_skill_migration_classification" not in text, f"runtime file {p} must not reference migration classification fixture"


# Revision-bound digest verification (sample full verification)
def test_w2_digest_full_verification(w2_data):
    rev = w2_data["source_revision"]
    # Full verification of all 28 via git show
    for entry in w2_data["entries"]:
        path = entry["source_path"]
        expected = entry["content_sha256"]
        try:
            out = subprocess.check_output(
                ["git", "-C", "/home/latios/workspace/aota-hermes-tools", "show", f"{rev}:{path}"],
                stderr=subprocess.DEVNULL
            )
            got = hashlib.sha256(out).hexdigest()
            assert got == expected, f"{entry['skill_name']} digest mismatch: got {got} expected {expected}"
        except subprocess.CalledProcessError as exc:
            pytest.fail(f"git show failed for {path} at {rev}: {exc}")
        assert SHA256_RE.fullmatch(expected)
        assert expected == expected.lower()


# Legacy repo safety: no write performed (checked via file existence)
def test_legacy_repo_safety(w2_data, w3_data):
    assert w2_data.get("aota_hermes_tools_write_performed") is False
    # w3 artifact should also declare no write
    assert w3_data["verification"]["legacy_repository_delta_created_by_w3"] is False
    # ensure legacy working tree still clean (no modifications to skills/*)
    result = subprocess.run(
        ["git", "-C", "/home/latios/workspace/aota-hermes-tools", "status", "--porcelain"],
        capture_output=True, text=True
    )
    porcelain = result.stdout
    for line in porcelain.splitlines():
        if "skills/" in line and line.strip():
            # If any skills modified, fail
            assert False, f"legacy repo has delta in skills/: {line}"

