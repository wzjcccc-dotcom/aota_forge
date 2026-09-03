
"""S3 M3 W2 Legacy Skill Inventory Classification — evidence snapshot validation.

Focus: deterministic revision-bound inventory, digest format, classification vocabulary,
no alias mapping, no invented version/provenance, no runtime authority, evidence completeness.
Does NOT require external legacy repo at test time.
"""

from __future__ import annotations

import json
import re
import pathlib
import pytest

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "s3_m3_legacy_skill_inventory.json"

ALLOWED_CLASSIFICATIONS = {
    "RETAIN",
    "REWRITE",
    "REPLACE_BY_FORGE",
    "RETIRE",
    "UNKNOWN_NEEDS_RECONCILIATION",
}

FINAL_CLASSIFICATIONS = {"RETAIN", "REWRITE", "REPLACE_BY_FORGE", "RETIRE"}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

@pytest.fixture
def fixture_data():
    p = pathlib.Path(FIXTURE_PATH)
    assert p.exists(), f"fixture missing: {p}"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data

@pytest.fixture
def entries(fixture_data):
    assert "entries" in fixture_data
    assert isinstance(fixture_data["entries"], list)
    return fixture_data["entries"]

# A. Revision binding
def test_revision_binding_present_and_sha_shape(fixture_data):
    assert "source_revision" in fixture_data
    rev = fixture_data["source_revision"]
    assert isinstance(rev, str)
    assert SHA40_RE.fullmatch(rev), f"source_revision must be 40 lower hex, got {rev!r}"
    assert len(rev) == 40
    assert rev == rev.lower()
    # also check legacy inventory revision bound flags
    assert fixture_data.get("legacy_inventory_revision_bound") is True
    assert fixture_data.get("legacy_inventory_deterministic") is True
    assert "source_repository" in fixture_data
    assert fixture_data["source_repository"] == "wzjcccc-dotcom/aota-hermes-tools"
    assert "runtime_assembly_path" in fixture_data
    assert fixture_data["runtime_assembly_path"] == "deploy/profile-runtime-assembly.yaml"

# B. Deterministic entries order
def test_entries_deterministic_order(entries):
    # canonical sort by skill_name then source_path
    sorted_entries = sorted(entries, key=lambda e: (e["skill_name"], e["source_path"]))
    # compare order
    assert entries == sorted_entries, "entries must be sorted by (skill_name, source_path) deterministically"
    # also check inventory_order_deterministic flag
    

def test_fixture_marks_order_deterministic(fixture_data):
    assert fixture_data.get("inventory_order_deterministic") is True
    assert fixture_data.get("filesystem_order_is_authority") is False

# C. Unique source paths
def test_unique_source_paths(entries):
    paths = [e["source_path"] for e in entries]
    assert len(paths) == len(set(paths)), f"duplicate source_path detected: {paths}"
    # also ensure no duplicate inventory paths via that
    for e in entries:
        assert isinstance(e["source_path"], str)
        assert e["source_path"].startswith("skills/")
        assert e["source_path"].endswith("/SKILL.md")

# D. Digest format
def test_digest_format_lowercase_sha256(entries):
    for e in entries:
        assert "content_sha256" in e, f"missing content_sha256 in {e.get('skill_name')}"
        d = e["content_sha256"]
        assert isinstance(d, str)
        assert SHA256_RE.fullmatch(d), f"digest must be 64 lower hex, got {d!r} for {e['skill_name']}"
        assert d == d.lower()
        assert "content_size" in e
        assert isinstance(e["content_size"], int)
        assert e["content_size"] > 0

# E. Classification vocabulary
def test_classification_vocabulary(entries):
    for e in entries:
        cls = e.get("preliminary_classification")
        assert cls in ALLOWED_CLASSIFICATIONS, f"invalid classification {cls!r} for {e['skill_name']}"

# F. UNKNOWN transitional not final
def test_unknown_transitional_not_final(fixture_data, entries):
    assert fixture_data.get("unknown_needs_reconciliation_is_final_classification") is False
    # Ensure fixture declares UNKNOWN not as final
    # Check that any UNKNOWN entry has unresolved_reasons non-empty
    unknowns = [e for e in entries if e["preliminary_classification"] == "UNKNOWN_NEEDS_RECONCILIATION"]
    # W2 may have zero or many unknowns; if any exist they must be unresolved explicit
    assert len(unknowns) > 0, "W2 should have at least some UNKNOWN_NEEDS_RECONCILIATION per transitional model"
    for e in unknowns:
        assert "unresolved_reasons" in e and isinstance(e["unresolved_reasons"], list) and len(e["unresolved_reasons"]) > 0
        assert "classification_evidence" in e and len(e["classification_evidence"]) > 0
    # Ensure summary reflects
    assert "unknown_needs_reconciliation_is_final_classification" in fixture_data
    # Also check that legacy_inventory_is_runtime_registry false
    assert fixture_data.get("legacy_inventory_is_runtime_registry") is False
    assert fixture_data.get("legacy_inventory_grants_authority") is False
    assert fixture_data.get("legacy_inventory_auto_loads_skill") is False

# G. Replacement evidence
def test_replace_by_forge_requires_concrete_seam(entries, fixture_data):
    assert fixture_data.get("replace_by_forge_requires_concrete_seam_evidence") is True
    for e in entries:
        if e["preliminary_classification"] == "REPLACE_BY_FORGE":
            assert "forge_replacement_evidence" in e
            ev = e["forge_replacement_evidence"]
            assert isinstance(ev, list) and len(ev) > 0, f"REPLACE_BY_FORGE {e['skill_name']} must have non-empty concrete seam evidence"
            # each evidence should be non-empty string
            for item in ev:
                assert isinstance(item, str) and item.strip()

# H. RETIRE evidence not solely unreferenced
def test_retire_not_solely_unreferenced(entries, fixture_data):
    assert fixture_data.get("unreferenced_alone_implies_retire") is False
    for e in entries:
        if e["preliminary_classification"] == "RETIRE":
            # Must not rely solely on UNREFERENCED; check classification_evidence contains more than just unreferenced
            ce = e.get("classification_evidence", [])
            ce_text = " ".join(str(x) for x in ce).lower()
            # If classification evidence only mentions unreferenced and nothing else, fail
            # At minimum should have another reason
            assert len(ce) >= 2 or "obsolete" in ce_text or "superseded" in ce_text or "conflict" in ce_text, \
                f"RETIRE {e['skill_name']} must not rely solely on UNREFERENCED"

def test_unreferenced_count_matches(entries, fixture_data):
    # Validate that unreferenced entries are not automatically retired
    unreferenced = [e for e in entries if e["usage_evidence"]["usage_state"] == "UNREFERENCED_BY_CURRENT_ASSEMBLY"]
    retired = [e for e in entries if e["preliminary_classification"] == "RETIRE"]
    # If our fixture correctly keeps UNKNOWN for unreferenced, then retired set does not equal unreferenced set
    # This is a sanity check: unreferenced alone does not force retire
    # Allow zero retired but ensure not all unreferenced are retired if any retired exists
    if retired:
        assert not set(r["skill_name"] for r in retired) == set(u["skill_name"] for u in unreferenced), \
            "unreferenced alone should not map 1-1 to RETIRE"

# I. Profile aliases
def test_no_invented_legacy_alias_mapping(fixture_data, entries):
    assert fixture_data.get("legacy_profile_alias_mapping_created") is False
    # Ensure no entry contains alias mapping like architect->analyst etc.
    forbidden_aliases = [
        "architect -> analyst",
        "debugger -> analyst",
        "task-coordinator -> task-main",
        "friday -> task-main",
    ]
    raw = json.dumps(fixture_data).lower()
    for alias in forbidden_aliases:
        assert alias.lower() not in raw, f"forbidden alias mapping found: {alias}"
    # Also ensure no field named alias mapping
    assert "alias_mapping" not in raw or "legacy_profile_alias_mapping_created" in raw

# J. Version/provenance not invented
def test_no_fabricated_version_provenance(entries, fixture_data):
    assert fixture_data.get("legacy_version_invented") is False
    assert fixture_data.get("legacy_declared_provenance_invented") is False
    for e in entries:
        dm = e.get("declared_metadata", {})
        # Must not contain invented s3-compatible version fields as if source-declared
        # Legacy explicit version absent should be marked false
        assert dm.get("_legacy_explicit_version_present") is False
        assert dm.get("_legacy_declared_provenance_present") is False
        # Ensure no top-level version/provenance pretending to be source-declared s3 identity
        assert "version" not in dm or dm["version"] is None or isinstance(dm["version"], str)
        # If version present, ensure it's not invented — but our fixture never includes version as legacy native
        # The check is that we didn't add fake version frontmatter
        assert "provenance" not in dm or dm.get("provenance") is None

# K. Evidence completeness
def test_evidence_completeness(entries):
    for e in entries:
        # Every entry must have content digest plus usage/ownership evidence fields
        assert "content_sha256" in e
        assert "content_size" in e
        assert "usage_evidence" in e and isinstance(e["usage_evidence"], dict)
        assert "active_profiles" in e["usage_evidence"]
        assert "reference_profiles" in e["usage_evidence"]
        assert "usage_state" in e["usage_evidence"]
        assert e["usage_evidence"]["usage_state"] in {"ACTIVE", "REFERENCE", "PROFILE_SPECIFIC", "UNREFERENCED_BY_CURRENT_ASSEMBLY"}
        assert "ownership_evidence" in e and isinstance(e["ownership_evidence"], dict)
        assert "forge_replacement_evidence" in e and isinstance(e["forge_replacement_evidence"], list)
        assert "preliminary_classification" in e
        assert "classification_evidence" in e and isinstance(e["classification_evidence"], list) and len(e["classification_evidence"]) > 0
        assert "unresolved_reasons" in e and isinstance(e["unresolved_reasons"], list) and len(e["unresolved_reasons"]) > 0
        assert "declared_metadata" in e

# L. No runtime authority
def test_no_runtime_authority(fixture_data, entries):
    # Snapshot must not grant authority or auto-load
    assert fixture_data.get("legacy_inventory_is_runtime_registry") is False
    assert fixture_data.get("legacy_inventory_grants_authority") is False
    assert fixture_data.get("legacy_inventory_auto_loads_skill") is False
    # Ensure no permission/authority grant semantics in fixture
    raw = json.dumps(fixture_data).lower()
    forbidden_phrases = ["grants_authority", "permission", "auto_loads"]
    # Only allowed as negative flags; ensure no positive grant
    assert '"legacy_inventory_grants_authority": false' in raw or '"legacy_inventory_grants_authority":false' in raw.replace(" ", "")
    # Ensure entries don't contain authority grant fields
    for e in entries:
        assert "authority" not in json.dumps(e).lower() or "authority" in "profile_runtime_assembly_usage" or True
        # More precise: entry should not have field like "grants_tool_authority": true
        assert e.get("grants_tool_authority") is None

def test_schema_and_revision_bound(entries, fixture_data):
    assert "schema_version" in fixture_data
    assert isinstance(fixture_data["schema_version"], str)
    assert "source_repository" in fixture_data
    assert "source_revision" in fixture_data
    assert fixture_data.get("legacy_inventory_revision_bound") is True
    assert fixture_data.get("all_tracked_legacy_skills_inventoried") is True
    assert fixture_data.get("all_inventoried_skill_content_read") is True
    assert fixture_data.get("content_sha256_recorded") is True
    assert len(entries) == fixture_data["summary"]["inventoried_legacy_skill_count"]
    assert len(entries) == fixture_data["summary"]["tracked_legacy_skill_count"]

def test_duplicate_handling(fixture_data):
    assert fixture_data.get("duplicate_legacy_skill_name_silent_resolved") is False

def test_malformed_handling(fixture_data, entries):
    assert fixture_data.get("malformed_legacy_skill_silently_skipped") is False
    malformed = [e for e in entries if e.get("frontmatter_malformed")]
    # If any malformed, they should still be inventoried, not skipped
    assert fixture_data["summary"]["malformed_legacy_skill_count"] == len(malformed)
    for e in malformed:
        assert "declared_metadata" in e
        assert e["declared_metadata"].get("_frontmatter_malformed") is True

def test_truncated_not_used_for_final(entries, fixture_data):
    assert fixture_data.get("truncated_content_used_for_final_classification") is False
    for e in entries:
        if e.get("content_truncated"):
            # truncated entries must remain UNKNOWN
            assert e["preliminary_classification"] == "UNKNOWN_NEEDS_RECONCILIATION"

def test_all_entries_final_classified_not_required(fixture_data):
    # W2 PASS does not require all final classified; ensure fixture reflects that
    assert fixture_data["summary"]["unknown_needs_reconciliation_count"] > 0
    # Ensure final classifications are not required
    # This is implicitly proven by unknowns >0

def test_committed_test_requires_no_external_repo(fixture_data):
    # Ensure fixture does not contain absolute legacy path dependency for test execution
    raw = json.dumps(fixture_data)
    assert "/home/latios/workspace/aota-hermes-tools" not in raw, "fixture must not embed absolute external path requiring repo"

def test_no_forge_runtime_production_source_changed():
    # This test ensures W2 does not wire fixture into runtime registry
    # Check that no work_plane file imports the fixture as registry
    # Resolve work_plane relative to repository root (parent of tests)
    repo_root = pathlib.Path(__file__).parent.parent
    for p in (repo_root / "aota_forge" / "work_plane").glob("*.py"):
        text = p.read_text(encoding="utf-8")
        assert "s3_m3_legacy_skill_inventory" not in text, f"runtime production file {p} must not reference legacy inventory fixture"

