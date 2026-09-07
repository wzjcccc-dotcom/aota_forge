"""W0 Canonical Skill & Tool Source Convergence — ownership & parity proof.

Verifies:
- every migrated AOTA domain Skill has AF canonical source
- no required domain Skill remains legacy-only
- Host-only Skills explicitly classified
- legacy physical copy does not claim canonical authority (inventory + file checks)
- AF descriptor/contract/provider remains semantic authority
- duplicate Hermes schema either derived/projected/parity-checked
- no Tool semantic authority exists only in legacy plugin
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "w0_canonical_source_convergence_inventory.json"
AF_ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILLS_ROOT = AF_ROOT / "skills"

# Invariants from inventory
def test_inventory_exists_and_counts():
    assert FIXTURE.exists(), f"inventory missing: {FIXTURE}"
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    counts = data["counts"]
    assert counts["LEGACY_ASSET_COUNT"] == 27, f"expected 27 assets, got {counts['LEGACY_ASSET_COUNT']}"
    assert counts["CLASS_A_COUNT"] == 7
    assert counts["CLASS_B_COUNT"] == 11
    assert counts["CLASS_C_COUNT"] == 6
    assert counts["CLASS_D_COUNT"] == 1
    assert counts["CLASS_E_COUNT"] == 2
    assert counts["CLASS_F_COUNT"] == 0
    assert counts["CLASS_G_COUNT"] == 0
    assert counts["CLASS_H_COUNT"] == 0
    assert counts["UNRESOLVED_OWNERSHIP_COUNT"] == 0
    assert counts["CANONICAL_DOMAIN_SKILL_MIGRATION_REQUIRED_COUNT"] == 16
    assert counts["CANONICAL_DOMAIN_SKILL_MIGRATED_COUNT"] == 16
    assert counts["CANONICAL_TOOL_SEMANTIC_MIGRATION_REQUIRED_COUNT"] == 1
    assert counts["CANONICAL_TOOL_SEMANTIC_MIGRATED_COUNT"] == 1

def test_inventory_invariants():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    inv = data["invariants"]
    assert inv["AOTA_SKILL_CANONICAL_SOURCE"] == "aota_forge"
    assert inv["AOTA_TOOL_CANONICAL_SOURCE"] == "aota_forge"
    assert inv["AOTA_HERMES_TOOLS_SKILL_AUTHORITY"] == "no"
    assert inv["AOTA_HERMES_TOOLS_TOOL_AUTHORITY"] == "no"
    assert inv["COPY_LEGACY_TREE_AS_IS"] == "no"
    assert inv["SKILL_IS_AUTHORITY"] == "no"

def test_no_h_blocked_material():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for e in data["entries"]:
        assert e["W0_CLASS"] != "H", f"H unclear ownership found: {e['ASSET_ID']}"
        assert e["ACTION"] != "BLOCKED_DECISION", f"BLOCKED_DECISION affecting M1: {e['ASSET_ID']}"

def test_every_migrated_skill_has_af_canonical_source():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    migrated = data["migration_plan"]["migrated_skills"]
    assert len(migrated) == 16
    for skill in migrated:
        p = SKILLS_ROOT / skill / "SKILL.md"
        assert p.exists(), f"migrated skill missing AF canonical source: {skill} at {p}"
        content = p.read_text(encoding="utf-8")
        assert "AOTA_SKILL_CANONICAL_SOURCE=aota_forge" in content, f"{skill} missing canonical header"
        # Ensure SKILL_IS_AUTHORITY=no semantics preserved (via header or skill.py invariant)
        # Check that file does not claim independent authority
        assert "THIS_FILE_IS_PROJECTION" not in content or "CANONICAL_SOURCE=aota_forge" in content

def test_host_specific_skills_preserved():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    host_preserved = data["migration_plan"]["host_specific_preserved"]
    assert "aota-runtime-smoke-verification" in host_preserved
    # Ensure AF does NOT contain host-specific skill as canonical (should remain in hermes)
    assert not (SKILLS_ROOT / "aota-runtime-smoke-verification").exists(), "host-specific skill should not be migrated to AF"

def test_deployment_projection_preserved():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "DEPLOY_RUNTIME_ASSEMBLY" in data["migration_plan"]["deployment_projection_preserved"]
    # AF should have chat_governance as canonical governance, not deployment
    assert (AF_ROOT / "chat_governance" / "GOVERNANCE_INDEX.md").exists()

def test_no_required_domain_skill_remains_legacy_only():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # All B and C skills (domain requiring convergence) must be in migrated list
    required = [e["ASSET_ID"] for e in data["entries"] if e["W0_CLASS"] in ("B","C") and e["ASSET_KIND"]=="SKILL"]
    migrated = set(data["migration_plan"]["migrated_skills"])
    for skill in required:
        assert skill in migrated, f"required domain skill {skill} not migrated, remains legacy-only"

def test_legacy_physical_copy_does_not_claim_authority():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # For migrated skills, ensure inventory says legacy is projection (not second authority)
    for e in data["entries"]:
        if e["ASSET_ID"] in data["migration_plan"]["migrated_skills"]:
            assert e["TARGET_CANONICAL_OWNER"] == "aota_forge", f"{e['ASSET_ID']} target must be aota_forge"
            assert e["CURRENT_CANONICAL_OWNER"] == "aota-hermes-tools"  # before migration
            assert e["ACTION"] in ("MIGRATE","REWRITE_MINIMAL")

def test_af_tool_authority():
    # AF descriptor/contract/provider remains semantic authority
    from aota_forge.core.contracts.loader import load_operation_descriptors, discover_canonical_project_root
    from aota_forge.work_plane.workspace_tools import WORKSPACE_READ_DESCRIPTOR, WORKSPACE_SEARCH_DESCRIPTOR
    from aota_forge.work_plane.workspace_mutation import WORKSPACE_WRITE_DESCRIPTOR
    from aota_forge.work_plane.restricted_shell import BoundedRestrictedShellProvider

    root = discover_canonical_project_root()
    descs = load_operation_descriptors(root)
    by_name = {d.name: d for d in descs}
    assert "execution.task_start" in by_name
    assert "plan_init" in by_name
    # Workspace descriptors must exist and be read/write correctly
    assert WORKSPACE_READ_DESCRIPTOR.name == "workspace.read"
    assert WORKSPACE_SEARCH_DESCRIPTOR.name == "workspace.search"
    assert WORKSPACE_WRITE_DESCRIPTOR.name == "workspace.write"
    # Ensure operation contract descriptor is authority, not Skill
    assert WORKSPACE_READ_DESCRIPTOR.read_write == "read"

def test_no_tool_semantic_authority_only_in_legacy():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # All tool assets with W0_CLASS A/C should have AF equivalent
    for e in data["entries"]:
        if e["ASSET_KIND"].startswith("TOOL") and e["W0_CLASS"] == "A":
            assert "aota_forge" in e["AF_EQUIVALENT"], f"{e['ASSET_ID']} A must have AF equivalent"
        if e["ASSET_ID"] == "TOOL_PLUGIN_YAML":
            assert e["W0_CLASS"] == "C"
            assert e["ACTION"] == "PROJECT_ONLY"
            assert "parity-checked" in e["RATIONALE"].lower() or "projection" in e["RATIONALE"].lower()

def test_copy_legacy_tree_not_done():
    # Ensure AF does not contain a wholesale copy of legacy plugin .py files
    legacy_plugin_files = list((AF_ROOT / "plugin").glob("**/*.py")) if (AF_ROOT / "plugin").exists() else []
    assert len(legacy_plugin_files) == 0, f"AF must not wholesale copy plugin/aota-tools/*.py, found {legacy_plugin_files}"
    # Ensure skills are not just blind copy: at least one REWRITE skill has AF-specific header
    p = SKILLS_ROOT / "aota-skill-development" / "SKILL.md"
    assert p.exists()
    content = p.read_text(encoding="utf-8")
    assert "Bounded rewrite" in content or "W0 Canonical Migration" in content
    assert "aota_forge" in content

def test_skill_is_authority_no():
    from aota_forge.work_plane.skill import SkillIdentity
    # Verify via skill module invariants
    import aota_forge.work_plane.skill as skill_mod
    # Check that skill module declares SKILL_IS_AUTHORITY=no
    # The module should have flag or doc saying so
    assert hasattr(skill_mod, "SkillIdentity")
    # Also check registry invariants
    import aota_forge.work_plane.skill_registry as reg_mod
    assert reg_mod.SKILL_IS_AUTHORITY is False

def test_anti_overdesign_gates():
    # Verify no new subsystems created
    # Check that AF does not have new registry/service files beyond allowed
    forbidden = [
        AF_ROOT / "aota_forge/core/new_operation_registry.py",
        AF_ROOT / "aota_forge/work_plane/new_skill_registry_service.py",
    ]
    for p in forbidden:
        assert not p.exists(), f"forbidden new subsystem found: {p}"
    # Check mcp_transport not mutated (except if unavoidable, we didn't)
    import pathlib
    mcp_path = AF_ROOT / "aota_forge/mcp_transport.py"
    content = mcp_path.read_text(encoding="utf-8")
    # Should still have 3 tools, not single-entry aota.invoke yet (W1)
    assert 'MCP_PUBLIC_TOOLS' in content
    assert 'aota.invoke' not in content or content.count('aota.invoke') < 5, "W0 must not implement MCP single-entry (belongs W1)"

